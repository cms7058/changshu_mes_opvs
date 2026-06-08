"""Document parsers: extract text + tables + images, generate HTML for preview.

Output:
- extracted_text: plain text (for LLM context)
- extracted_html: HTML that visually approximates the original layout, with
                  <img src="/api/documents/{id}/asset/{name}"> for embedded images
- asset_dir: relative directory under UPLOAD_DIR holding extracted images
"""
from __future__ import annotations
import os, html, re, zipfile, uuid, base64, mimetypes
from xml.etree import ElementTree as ET
from typing import Tuple


def _img_to_data_uri(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    mime = mime or "image/png"
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{data}"

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DRAW_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"

W = lambda tag: f"{{{WORD_NS}}}{tag}"
R = lambda tag: f"{{{REL_NS}}}{tag}"


# ============== DOCX ==============

def _docx_load_rels(z: zipfile.ZipFile) -> dict[str, str]:
    """Return rId → target path (e.g. 'media/image1.png')."""
    rels = {}
    try:
        rel_xml = z.read("word/_rels/document.xml.rels").decode("utf-8")
        root = ET.fromstring(rel_xml)
        for rel in root:
            rels[rel.attrib.get("Id")] = rel.attrib.get("Target")
    except KeyError:
        pass
    return rels


def _docx_image_id(elem) -> str | None:
    """Find a:blip r:embed in a drawing element."""
    for blip in elem.iter("{%s}blip" % DRAW_NS):
        return blip.attrib.get(f"{{{REL_NS}}}embed")
    return None


def _docx_para_html(p, rels, image_data_uris) -> str:
    """Convert a <w:p> to inline HTML; emits <img> for embedded drawings."""
    parts = []
    text_buf = []
    for el in p.iter():
        tag = el.tag.split("}")[-1]
        if tag == "t":
            if el.text:
                text_buf.append(el.text)
        elif tag == "drawing":
            # flush text
            if text_buf:
                parts.append(html.escape("".join(text_buf)))
                text_buf = []
            rid = _docx_image_id(el)
            if rid and rid in rels:
                target = rels[rid]
                base = os.path.basename(target)
                if base in image_data_uris:
                    parts.append(f'<img src="{image_data_uris[base]}" class="doc-img" alt="{base}">')
    if text_buf:
        parts.append(html.escape("".join(text_buf)))
    body = "".join(parts).strip()
    if not body:
        return ""

    ppr = p.find(W("pPr"))
    heading = None
    centered = False
    if ppr is not None:
        pstyle = ppr.find(W("pStyle"))
        if pstyle is not None:
            v = pstyle.attrib.get(W("val"), "")
            m = re.match(r"Heading(\d)", v)
            if m: heading = int(m.group(1))
        jc = ppr.find(W("jc"))
        if jc is not None and jc.attrib.get(W("val")) == "center":
            centered = True
    cls = ' class="center"' if centered else ""
    if heading:
        return f"<h{heading}{cls}>{body}</h{heading}>"
    return f"<p{cls}>{body}</p>"


def _docx_table_html(tbl) -> str:
    rows_html = []
    for tr in tbl.findall(W("tr")):
        cells = []
        for tc in tr.findall(W("tc")):
            tcpr = tc.find(W("tcPr"))
            fill = None
            if tcpr is not None:
                shd = tcpr.find(W("shd"))
                if shd is not None:
                    f = shd.attrib.get(W("fill"))
                    if f and f != "auto":
                        fill = f.upper()
            inner = []
            for p in tc.findall(W("p")):
                texts = []
                for t in p.iter(W("t")):
                    if t.text: texts.append(t.text)
                if texts:
                    inner.append(html.escape("".join(texts)))
            content = "<br>".join(inner)
            cls = ""
            if fill == "DCE7F5": cls = ' class="cell-new"'
            elif fill == "FFF2CC": cls = ' class="cell-rev"'
            elif fill == "B4C7E7": cls = ' class="cell-head"'
            cells.append(f"<td{cls}>{content}</td>")
        rows_html.append("<tr>" + "".join(cells) + "</tr>")
    return '<table class="doc-tbl">' + "".join(rows_html) + "</table>"


def parse_docx(file_path: str, asset_out_dir: str) -> Tuple[str, str]:
    """Return (text, html). Images are extracted to disk AND inlined as data URIs."""
    os.makedirs(asset_out_dir, exist_ok=True)
    image_data_uris: dict[str, str] = {}

    with zipfile.ZipFile(file_path, "r") as z:
        for name in z.namelist():
            if name.startswith("word/media/"):
                fname = os.path.basename(name)
                if not fname: continue
                abs_path = os.path.join(asset_out_dir, fname)
                with z.open(name) as src, open(abs_path, "wb") as dst:
                    dst.write(src.read())
                image_data_uris[fname] = _img_to_data_uri(abs_path)
        rels = _docx_load_rels(z)
        xml = z.read("word/document.xml").decode("utf-8")

    root = ET.fromstring(xml)
    body = root.find(W("body"))
    html_parts = []
    text_parts = []

    for child in body:
        tag = child.tag.split("}")[-1]
        if tag == "p":
            h = _docx_para_html(child, rels, image_data_uris)
            if h: html_parts.append(h)
            # plain text for LLM
            texts = []
            for t in child.iter(W("t")):
                if t.text: texts.append(t.text)
            if texts: text_parts.append("".join(texts))
        elif tag == "tbl":
            html_parts.append(_docx_table_html(child))
            # plain text for table
            rows = []
            for tr in child.findall(W("tr")):
                cells = []
                for tc in tr.findall(W("tc")):
                    texts = []
                    for t in tc.iter(W("t")):
                        if t.text: texts.append(t.text)
                    cells.append("".join(texts))
                rows.append(" | ".join(cells))
            text_parts.append("\n".join(rows))

    return "\n".join(text_parts), "\n".join(html_parts)


# ============== PPTX ==============

PPT_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

def parse_pptx(file_path: str, asset_out_dir: str) -> Tuple[str, str]:
    """Parse pptx slides. Each slide → <section class='slide'>...</section>."""
    os.makedirs(asset_out_dir, exist_ok=True)
    image_data_uris: dict[str, str] = {}

    with zipfile.ZipFile(file_path, "r") as z:
        for name in z.namelist():
            if name.startswith("ppt/media/"):
                fname = os.path.basename(name)
                if not fname: continue
                abs_path = os.path.join(asset_out_dir, fname)
                with z.open(name) as src, open(abs_path, "wb") as dst:
                    dst.write(src.read())
                image_data_uris[fname] = _img_to_data_uri(abs_path)

        slide_names = sorted(
            [n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)],
            key=lambda n: int(re.search(r"slide(\d+)", n).group(1)),
        )

        html_parts = []
        text_parts = []
        for idx, sn in enumerate(slide_names, 1):
            # Load slide rels
            rels_path = f"ppt/slides/_rels/{os.path.basename(sn)}.rels"
            rid_to_img = {}
            try:
                rel_xml = z.read(rels_path).decode("utf-8")
                for rel in ET.fromstring(rel_xml):
                    target = rel.attrib.get("Target", "")
                    if "media/" in target:
                        rid_to_img[rel.attrib.get("Id")] = os.path.basename(target)
            except KeyError:
                pass

            xml = z.read(sn).decode("utf-8")
            root = ET.fromstring(xml)
            slide_text = []
            slide_html = [f'<section class="slide"><div class="slide-no">Slide {idx}</div>']
            # texts
            for t in root.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t"):
                if t.text:
                    slide_text.append(t.text)
                    slide_html.append(f"<p>{html.escape(t.text)}</p>")
            # images
            for blip in root.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}blip"):
                rid = blip.attrib.get(f"{{{PPT_NS_REL}}}embed")
                if rid and rid in rid_to_img:
                    fname = rid_to_img[rid]
                    if fname in image_data_uris:
                        slide_html.append(f'<img src="{image_data_uris[fname]}" class="doc-img" alt="{fname}">')
            slide_html.append("</section>")
            html_parts.append("".join(slide_html))
            text_parts.append(f"[Slide {idx}]\n" + "\n".join(slide_text))

    return "\n\n".join(text_parts), "\n".join(html_parts)


# ============== Dispatcher ==============

SUPPORTED = {".docx", ".pptx"}

def parse_file(file_path: str, asset_out_dir: str) -> dict:
    """Return {status, text, html, error}."""
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".docx":
            t, h = parse_docx(file_path, asset_out_dir)
        elif ext == ".pptx":
            t, h = parse_pptx(file_path, asset_out_dir)
        else:
            return {"status": "unsupported", "text": "", "html": "", "error": f"未支持的格式: {ext}"}
        return {"status": "done", "text": t, "html": h, "error": None}
    except Exception as e:
        return {"status": "failed", "text": "", "html": "", "error": str(e)}
