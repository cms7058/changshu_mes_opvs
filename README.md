# MES 运维智能体 · P1 骨架

面向 MES/WMS 实施与运维场景的内部工具：把客户通过对话、Word、PPT 提出的问题，结构化沉淀为「项目空间 + 文档 + 问题 + 方案 + 知识库」，配合 MiniMax 大模型做问题分析与方案生成。

---

## P1 已交付能力

- ✅ **用户管理**：JWT 登录，三角色（admin / engineer / viewer），项目级权限
- ✅ **项目空间**：多项目隔离，按用户授权访问
- ✅ **文档管理**：docx/pptx/pdf 上传、列表、下载、删除（本地文件系统）
- ✅ **MiniMax 对接**：Anthropic-compatible 端点，支持普通+流式
- ✅ **AI 对话**：基于项目空间的会话窗，对话历史持久化
- ✅ **审计日志**：所有写操作留痕
- ✅ **单机部署**：systemd 守护，无需 Docker，专为 2C2G 优化

后续阶段：P2 文档解析+自动抽问题、P3 RAG 知识库、P4 蓝图自动生成。

---

## 技术栈

| 组件 | 选型 | 理由 |
|---|---|---|
| 语言 | Python 3.11 | docx/pptx 解析生态最好 |
| Web 框架 | FastAPI + uvicorn | 异步、Swagger 自带 |
| ORM | SQLModel | Pydantic + SQLAlchemy 二合一 |
| 数据库 | SQLite（WAL） | 单文件、零运维、2GiB 不吃力 |
| 文件存储 | 本地 FS（`./uploads/`） | 省 MinIO 进程 |
| 鉴权 | bcrypt + JWT | 经典稳定 |
| LLM | MiniMax M2.7（Anthropic 兼容） | 用 anthropic SDK 直连 |
| 守护 | systemd | 比 Docker 省内存 |
| 反代 | Nginx | 标准 |

---

## 本地开发

```bash
cd mes-agent
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，填入 MINIMAX_API_KEY
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

浏览器访问 `http://localhost:8000/`，用 `.env` 里的 `ADMIN_USERNAME / ADMIN_PASSWORD` 登录。

API 文档自动暴露在 `http://localhost:8000/docs`。

---

## 阿里云部署（Alibaba Cloud Linux 3 / Ubuntu 22.04）

推荐**走 git 部署**，便于后续增量更新。下面以 **Gitee** 为例（GitHub / 阿里云 CodeUp 流程一致，只换 URL）。

### A. 一次性准备（本地，把代码推到 Gitee）

```bash
cd /Users/mingyue/changshu_mes/mes-agent

# 已经 git init 过，可直接：
git add -A
git commit -m "P1: skeleton + user mgmt + minimax + docs"
git branch -M main

# 在 Gitee 网页创建一个**私有**仓库（名字 mes-agent），然后：
git remote add origin git@gitee.com:<你的账号>/mes-agent.git
git push -u origin main
```

> 私有仓的 SSH key 配置：Gitee → 个人设置 → SSH 公钥 → 粘贴 `~/.ssh/id_ed25519.pub`。
> 不想用 SSH 用 HTTPS 也行，clone 时用 `https://gitee.com/账号/mes-agent.git`，会在 server 上提示输用户名密码（建议生成"私人令牌"代替密码）。

### B. 服务器首次部署（git clone + 一键脚本）

```bash
# SSH 登录 ECS
ssh root@<your-ecs-ip>

# 安装 git（如果没有）
sudo dnf install -y git  # AliLinux/CentOS 系
# 或：sudo apt-get install -y git  # Ubuntu

# 把仓库 URL 传给 setup.sh，脚本会自动 clone + 装依赖 + 配 systemd + Nginx
export GIT_REPO=https://gitee.com/<你的账号>/mes-agent.git
export GIT_BRANCH=main

# 临时拉一次 setup.sh 跑
curl -fsSL "https://gitee.com/<你的账号>/mes-agent/raw/main/deploy/setup.sh" -o /tmp/setup.sh
sudo -E bash /tmp/setup.sh
```

如果是**私有仓**，HTTPS clone 会要凭证：

```bash
# 方案 1：把令牌嵌进 URL（最简单）
export GIT_REPO=https://oauth2:<gitee-token>@gitee.com/<你的账号>/mes-agent.git

# 方案 2：配置 mesagent 用户的 SSH key 推到 Gitee
sudo -u mesagent ssh-keygen -t ed25519 -N '' -f ~mesagent/.ssh/id_ed25519
sudo cat ~mesagent/.ssh/id_ed25519.pub   # 复制到 Gitee 个人 SSH key
export GIT_REPO=git@gitee.com:<你的账号>/mes-agent.git
```

### C. 编辑环境变量 + 启动

```bash
sudo vim /opt/mes-agent/.env
#   - 填入 MINIMAX_API_KEY
#   - 改掉 ADMIN_PASSWORD
#   - JWT_SECRET 已被 setup.sh 自动随机

sudo systemctl start mes-agent
sudo systemctl status mes-agent
sudo journalctl -u mes-agent -f
sudo tail -f /var/log/mes-agent/app.log
```

浏览器访问 `http://<your-ecs-ip>/`，默认登录 `admin / ChangeMe!2026`。

### D. 后续更新（一行命令）

本地推代码：
```bash
git add -A && git commit -m "fix: xxx" && git push
```

服务器拉取并重启：
```bash
sudo bash /opt/mes-agent/deploy/update.sh
```

`update.sh` 会自动：
- `git pull` 最新代码
- 若 `requirements.txt` 变了，自动 `pip install`
- 重启 systemd 服务并打印状态
- 若没有任何变化，直接退出（不重启）

### E. 回滚

```bash
# 看历史
sudo -u mesagent git -C /opt/mes-agent log --oneline -10
# 回到指定 commit
sudo -u mesagent git -C /opt/mes-agent reset --hard <commit-hash>
sudo systemctl restart mes-agent
```

### F. 替换 git 托管平台

只改 `GIT_REPO` 环境变量即可，下面是几个示例：

| 平台 | URL 格式 |
|---|---|
| Gitee（推荐） | `https://gitee.com/账号/mes-agent.git` |
| 阿里云 CodeUp | `https://codeup.aliyun.com/<org>/<repo>.git` |
| GitHub | `https://github.com/账号/mes-agent.git`（国内 ECS 拉取慢，建议加代理） |
| 自建 Gitea | `https://git.your-domain.com/账号/mes-agent.git` |

### 资源占用（2C2G 实测预期）
| 进程 | RSS |
|---|---|
| uvicorn + FastAPI | ~180 MB |
| nginx | ~30 MB |
| sqlite（嵌在 python 进程里） | 0（已计入） |
| 文档解析峰值额外 | ~150 MB |
| **总计** | **~360 MB 平时 / 510 MB 峰值** |

剩余 ~1.5 GB 给 OS + 未来组件（向量库等）。

---

## 目录结构

```
mes-agent/
├── app/
│   ├── main.py              FastAPI 入口
│   ├── config.py            .env 配置
│   ├── db.py                SQLite 初始化 + 自动建管理员
│   ├── auth.py              bcrypt + JWT + 权限依赖
│   ├── models.py            8 张表：User/Project/UserProject/Document/Issue/ChatSession/ChatMessage/AuditLog
│   ├── schemas.py           Pydantic 请求/响应
│   ├── llm.py               MiniMax 客户端
│   ├── routers/
│   │   ├── auth.py          POST /api/auth/login, GET /api/auth/me
│   │   ├── users.py         CRUD /api/users (admin only)
│   │   ├── projects.py      CRUD /api/projects + 成员授权
│   │   ├── documents.py     上传/下载/删除
│   │   └── chat.py          /api/chat 调用 MiniMax，附健康检查
│   └── static/
│       ├── login.html
│       └── index.html       SPA 主页（侧栏导航 + 项目/文档/对话/用户管理）
├── deploy/
│   ├── mes-agent.service    systemd unit
│   ├── nginx.conf           Nginx 反向代理模板
│   └── setup.sh             一键安装脚本
├── data/                    SQLite 数据文件（gitignored）
├── uploads/                 上传文档存储（gitignored）
├── .env.example
├── requirements.txt
└── README.md
```

---

## 默认管理员账号

| 字段 | 默认值 |
|---|---|
| 用户名 | `admin` |
| 密码 | `ChangeMe!2026` ← **首次登录后立即改！** |
| 角色 | `admin` |

修改方式：登录后 → 侧栏「用户管理」→ 编辑 admin → 设置新密码。

---

## 角色权限

| 操作 | admin | engineer | viewer |
|---|:-:|:-:|:-:|
| 用户管理 | ✅ | ❌ | ❌ |
| 创建项目 | ✅ | ✅ | ❌ |
| 授权用户加入项目 | ✅ | ❌ | ❌ |
| 上传文档 | ✅ | ✅（需项目权限） | ❌ |
| AI 对话 | ✅ | ✅（需项目权限） | ❌ |
| 查看文档列表 | ✅ | ✅（需项目权限） | ✅（需项目权限） |
| 系统健康检查 | ✅ | ❌ | ❌ |

---

## 安全注意事项

1. `.env` 文件权限已设 600，且 git 已忽略，**不要提交到代码库**
2. JWT_SECRET 已由 setup.sh 随机生成，无需手动改
3. MINIMAX_API_KEY 请轮换：如果你的旧 key 曾在对话/邮件中暴露，去 MiniMax 控制台立即作废
4. 生产环境强烈建议配 HTTPS（`certbot --nginx -d your-domain`）
5. 阿里云安全组：只开 22（SSH，限信任 IP）+ 80/443（公网或内网）

---

## 下一步（P2 预告）

- 上传 docx/pptx 后自动解析、抽取问题（结构化）
- 把现有常熟 WMS 项目的 10 份蓝图作为种子知识灌入
- "问题诊断"工作流：粘贴客户问题 → AI 输出问题分类 + 初步方案 + 需要客户提供的输入
