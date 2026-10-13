import json
import sys
from pathlib import Path

def build_document(template_name, title, output_name="output.hwpx"):
    templates_base = Path(__file__).parent.parent / "4_형식템플릿"
    candidates = list(templates_base.glob(f"**/{template_name}*.json"))
    if not candidates:
        print(f"Template not found: {template_name}")
        return
    
    tpl_path = candidates[0]
    print(f"Loading template: {tpl_path.name}")
    with open(tpl_path, "r", encoding="utf-8") as f:
        job = json.load(f)
        
    job["output"] = str(Path(output_name).resolve())
    # Quick title replace
    for b in job.get("blocks", []):
        if b.get("type") == "paragraph" and "계획" in b.get("text", ""):
            b["text"] = title
            
    out_job = Path("temp_job.json")
    with open(out_job, "w", encoding="utf-8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)
    print(f"Job created: {out_job}")

if __name__ == "__main__":
    if len(sys.argv) > 2:
        build_document(sys.argv[1], sys.argv[2])
    else:
        print("Usage: python template_builder.py <template_keyword> <new_title> [output.hwpx]")
