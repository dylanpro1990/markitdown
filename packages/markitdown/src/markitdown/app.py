from __future__ import annotations

import io
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from markitdown import MarkItDown
else:
    from . import MarkItDown


def _app_temp_dir() -> Path:
    temp_dir = Path.cwd() / ".markitdown-app-temp"
    temp_dir.mkdir(exist_ok=True)
    return temp_dir


def _safe_markdown_name(filename: str) -> str:
    source = Path(filename)
    stem = source.stem or "document"
    return f"{stem}.md"


def _convert_uploaded_file(uploaded_file: Any, converter: MarkItDown) -> tuple[str, str]:
    suffix = Path(uploaded_file.name).suffix
    with tempfile.NamedTemporaryFile(
        delete=False, suffix=suffix, dir=_app_temp_dir()
    ) as temp_file:
        temp_file.write(uploaded_file.getvalue())
        temp_path = temp_file.name

    try:
        result = converter.convert(temp_path)
        return _safe_markdown_name(uploaded_file.name), result.markdown
    finally:
        Path(temp_path).unlink(missing_ok=True)


def _build_zip(results: list[dict[str, str]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in results:
            archive.writestr(item["output_name"], item["markdown"])
    buffer.seek(0)
    return buffer.getvalue()


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="MarkItDown App", layout="wide")
    st.title("MarkItDown")
    st.caption("Drag files here and convert them to Markdown.")
    st.info("Large PDFs can take a little while. After you click convert, wait for the status message to finish.")

    with st.sidebar:
        st.subheader("How to use")
        st.write("1. Drag one or more files into the upload area.")
        st.write("2. Click **Convert to Markdown**.")
        st.write("3. Preview the output and download the `.md` files.")

    uploaded_files = st.file_uploader(
        "Drop files here",
        accept_multiple_files=True,
        help="PDF, Word, Excel, PowerPoint, HTML, JSON, images and more.",
    )

    if "conversion_results" not in st.session_state:
        st.session_state["conversion_results"] = []
    if "conversion_failures" not in st.session_state:
        st.session_state["conversion_failures"] = []

    if st.button(
        "Convert to Markdown",
        type="primary",
        disabled=not uploaded_files,
        use_container_width=True,
    ):
        converter = MarkItDown()
        results: list[dict[str, str]] = []
        failures: list[str] = []
        status = st.empty()
        progress = st.progress(0.01)

        with st.spinner("Converting files..."):
            total_files = len(uploaded_files or [])
            for index, uploaded_file in enumerate(uploaded_files or [], start=1):
                current_progress = (index - 1) / total_files
                progress.progress(max(current_progress, 0.05))
                status.info(f"Converting {uploaded_file.name} ({index}/{total_files})...")

                try:
                    output_name, markdown = _convert_uploaded_file(uploaded_file, converter)
                    results.append(
                        {
                            "source_name": uploaded_file.name,
                            "output_name": output_name,
                            "markdown": markdown,
                        }
                    )
                except Exception as exc:
                    failures.append(f"{uploaded_file.name}: {exc}")

                progress.progress(index / total_files)

        st.session_state["conversion_results"] = results
        st.session_state["conversion_failures"] = failures

        if results:
            st.success(f"Converted {len(results)} file(s).")
        if failures:
            st.error("Some files could not be converted:")
            for failure in failures:
                st.write(f"- {failure}")
        if not failures:
            status.success("Conversion finished.")

    results = st.session_state.get("conversion_results", [])
    failures = st.session_state.get("conversion_failures", [])
    if failures:
        st.error("Some files could not be converted:")
        for failure in failures:
            st.write(f"- {failure}")

    if results:
        archive_bytes = _build_zip(results)
        st.download_button(
            "Download all as ZIP",
            data=archive_bytes,
            file_name="markitdown-results.zip",
            mime="application/zip",
            use_container_width=True,
        )

        for item in results:
            with st.expander(item["source_name"], expanded=True):
                st.download_button(
                    f"Download {item['output_name']}",
                    data=item["markdown"],
                    file_name=item["output_name"],
                    mime="text/markdown",
                    key=f"download-{item['output_name']}",
                )
                st.text_area(
                    f"Preview: {item['output_name']}",
                    item["markdown"],
                    height=280,
                    key=f"preview-{item['output_name']}",
                )


def run() -> int:
    from streamlit.web import cli as stcli

    sys.argv = ["streamlit", "run", __file__]
    return stcli.main()


if __name__ == "__main__":
    main()
