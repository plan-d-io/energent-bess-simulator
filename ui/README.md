# Streamlit interface

`ui/app.py` is the supported Streamlit entry point. The interface calls public
`btm_sim` services, launches frozen worker requests, and renders verified result
artifacts. It does not implement battery physics, optimization objectives, or
financial calculations.

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m streamlit run ui\app.py
.\.venv\Scripts\python.exe -m pytest ui\tests -q
```

The simulator version is stored in `src/btm_sim/VERSION`. The independent
front-end version is stored in `ui/VERSION`.

Project authorship is recorded in [`AUTHORS.md`](../AUTHORS.md).

Saved demonstration artifacts live under `ui/demo_artifacts/`. Their manifests
are verified before the UI opens them. Do not replace them with unreviewed run
folders or raw customer data.
