"""Discover and call installed Hancom COM features with declarative JSON jobs."""
from __future__ import annotations
import json
import os
from pathlib import Path
from .hwp_automation_bridge import HancomBridge, HancomError


def members(obj):
    info = obj._oleobj_.GetTypeInfo()
    result = []
    for index in range(info.GetTypeAttr().cFuncs):
        func = info.GetFuncDesc(index)
        names = info.GetNames(func.memid)
        if names[0] in ("QueryInterface", "AddRef", "Release", "GetTypeInfoCount", "GetTypeInfo", "GetIDsOfNames", "Invoke"):
            continue
        result.append({"name": names[0], "arguments": list(names[1:]), "kind": {1:"method",2:"get",4:"set",8:"set_ref"}.get(func.invkind,str(func.invkind))})
    return result


def serializable(value):
    if value is None or isinstance(value,(str,int,float,bool)): return value
    if isinstance(value,(list,tuple)): return [serializable(v) for v in value]
    if hasattr(value,"_oleobj_"):
        declaration = members(value)
        result = {"com_object": value._oleobj_.GetTypeInfo().GetDocumentation(-1)[0], "members": declaration}
        if any(m["name"] == "SetID" and m["kind"] == "get" for m in declaration): result["set_id"] = value.SetID
        return result
    raise HancomError("COM result cannot be represented as JSON")


def capabilities(bridge, action_names=None):
    catalog_path = Path(__file__).with_name("hancom_actions.json")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    sets = {}
    for member in members(bridge.hwp.HParameterSet):
        if member["kind"] == "get" and member["name"].startswith("H"):
            wrapper = getattr(bridge.hwp.HParameterSet,member["name"])
            sets[member["name"]] = sorted({m["name"] for m in members(wrapper) if m["kind"] in ("get","set") and m["name"]!="HSet"})
    actions = []
    for name in action_names or []:
        action=bridge.hwp.CreateAction(name)
        if action is None: actions.append({"name":name,"available":False}); continue
        pset=action.CreateSet()
        entry={"name":name,"available":True,"enabled_in_current_context":bool(bridge.hwp.IsActionEnable(name))}
        if pset is not None:
            bridge.check(action.GetDefault(pset),f"GetDefault {name}")
            entry["parameter_set"] = pset.SetID
            keys=sets.get("H"+str(pset.SetID),[])
            canonical_keys = {key if pset.ItemExist(key) else key[0].upper()+key[1:] for key in keys}
            entry["parameter_keys"] = sorted(canonical_keys)
            entry["defaults"]={key:serializable(pset.Item(key)) for key in canonical_keys if pset.ItemExist(key)}
        actions.append(entry)
    return {"hancom_version":bridge.hwp.Version,"security_module_registered":bridge.security_module_registered,
            "members":members(bridge.hwp),"parameter_sets":sets,"action_catalog":catalog,"requested_actions":actions,
            "coverage":"COM declarations and documented action identifiers; availability depends on the installed version and cursor context",
            "all_hancom_features_verified":False}


def assertion(bridge, spec):
    from .automation import _keys
    _keys(spec, ("op", "kind", "expected", "text", "table_index", "row", "col", "field", "name", "ctrl_id", "args"), "Hancom assertion")
    kind=spec.get("kind")
    expected=spec.get("expected")
    if kind=="text_contains": actual=spec.get("text") in bridge.snapshot()["text"]; expected=True
    elif kind=="cell_text": actual=bridge.cell(bridge.table(spec.get("table_index",0)),spec["row"],spec["col"])["text"]
    elif kind=="table_rows": actual=bridge.table(spec.get("table_index",0))["rows"]
    elif kind=="table_cols": actual=bridge.table(spec.get("table_index",0))["cols"]
    elif kind=="cell_span":
        cell=bridge.cell(bridge.table(spec.get("table_index",0)),spec["row"],spec["col"])
        actual=[cell["row_span"],cell["col_span"]]
    elif kind=="field_text": actual=bridge.hwp.GetFieldText(spec["field"])
    elif kind=="page_count": actual=bridge.hwp.PageCount
    elif kind=="control_count": actual=len(bridge.controls(spec["ctrl_id"]))
    elif kind=="method_result":
        name=spec["name"]
        if not name.startswith(("Get", "Is")) or not any(m["name"]==name and m["kind"]=="method" for m in members(bridge.hwp)): raise HancomError("Verification requires a declared Get/Is method")
        actual=serializable(getattr(bridge.hwp,name)(*spec.get("args",[])))
    elif kind=="property":
        name=spec["name"]
        if not any(m["name"]==name and m["kind"]=="get" for m in members(bridge.hwp)): raise HancomError("Unknown readable property")
        actual=serializable(getattr(bridge.hwp,name))
    else: raise HancomError(f"Unknown assertion kind: {kind}")
    if actual!=expected: raise HancomError(f"Verification {kind} failed: expected {expected!r}, got {actual!r}")
    return {"kind":kind,"passed":True,"actual":actual}


def operations(bridge, ops):
    if not isinstance(ops,list): raise HancomError("operations must be an array")
    from .automation import _keys
    objects = {}
    def argument(value):
        if isinstance(value,dict):
            if set(value)=={"$ref"}:
                if value["$ref"] not in objects: raise HancomError("Unknown COM object reference")
                return objects[value["$ref"]]
            if set(value)=={"$set","items"}:
                pset=bridge.hwp.CreateSet(value["$set"])
                bridge.set_parameters(pset,value["items"])
                return pset
            raise HancomError("COM object arguments require $ref or $set/items")
        if isinstance(value,list): return [argument(v) for v in value]
        return value
    for index,op in enumerate(ops):
        if not isinstance(op,dict): raise HancomError("operation must be an object")
        bridge.activate_document()
        kind=op.get("op")
        if kind=="hancom_action":
            _keys(op,("op","action","parameters"),"Hancom action")
            result=bridge.execute_action(op["action"],op.get("parameters"))
        elif kind in ("hancom_method","hancom_get","hancom_set"):
            _keys(op,("op","name","args","object","store","value","expected_return"),"COM operation")
            name=op["name"]
            obj=objects.get(op.get("object")) if "object" in op else bridge.hwp
            if obj is None: raise HancomError("Unknown COM object reference")
            declared=members(obj)
            # Lifecycle belongs to the transaction. These actions would escape it.
            if name in ("Open","Save","SaveAs","Quit","Clear","RegisterModule"):
                raise HancomError(f"{name} is managed by the job lifecycle")
            member_kind={"hancom_method":"method","hancom_get":"get","hancom_set":"set"}[kind]
            if not any(m["name"]==name and m["kind"]==member_kind for m in declared): raise HancomError(f"Unknown {member_kind}: {name}")
            if kind=="hancom_method":
                args=op.get("args",[])
                if not isinstance(args,list): raise HancomError("args must be an array")
                result=getattr(obj,name)(*(argument(v) for v in args))
                if "expected_return" not in op: bridge.check(result,name)
            elif kind=="hancom_get":
                result=getattr(obj,name)
                if "args" in op: result=result(*(argument(v) for v in op["args"]))
            else: setattr(obj,name,argument(op["value"])); result=getattr(obj,name)
            if "expected_return" in op and serializable(result)!=op["expected_return"]: raise HancomError(f"Unexpected return value: {name}")
            if "store" in op:
                if not hasattr(result,"_oleobj_"): raise HancomError("store requires a COM object result")
                objects[op["store"]]=result
        elif kind=="hancom_parameter_set":
            _keys(op,("op","set_type","parameters","store"),"COM parameter set")
            result=bridge.hwp.CreateSet(op["set_type"])
            bridge.set_parameters(result,op.get("parameters",{}))
            objects[op["store"]]=result
        elif kind=="hancom_move_cell":
            _keys(op,("op","table_index","row","col"),"COM cell selector")
            result=bridge.move_to_cell(op.get("table_index",0),op["row"],op["col"])
        elif kind=="hancom_assert": result=assertion(bridge,op)
        else:
            bridge.apply_operations_on_hwp([op]); continue
        bridge.receipts.append({"index":index,"op":kind,"return":serializable(result),"runtime_checked":True})


def run_hancom_job(job,base):
    from .automation import _keys,_path,_stage,_sha,_bools
    action=job["action"]
    _keys(job,("schema","action","input","output","format","operations","verify","action_names","visible","overwrite_output","overwrite_source","expected_input_sha256"),"Hancom job")
    _bools(job,("overwrite_output","overwrite_source","visible"),"Hancom job")
    source=_path(job["input"],base,"input") if "input" in job else None
    source_hash=_sha(source) if source else None
    if "expected_input_sha256" in job and source_hash!=job["expected_input_sha256"].lower(): raise HancomError("Input SHA-256 changed")
    if action=="hancom_capabilities":
        _keys(job,("schema","action","input","action_names","visible","expected_input_sha256"),"capabilities job")
        names = job.get("action_names", [])
        if not isinstance(names, list) or any(not isinstance(n, str) for n in names): raise HancomError("action_names must be an array of identifiers")
        with HancomBridge(visible=job.get("visible",False)) as bridge:
            if source: bridge.open_doc(source)
            return {"schema":1,"action":action,"success":True,"uses_hancom_runtime":True,**capabilities(bridge,job.get("action_names"))}
    target=_path(job.get("output"),base,"output")
    if target.suffix.lower() not in (".hwp",".hwpx"): raise HancomError("Hancom output must end in .hwp or .hwpx")
    if "format" in job and job["format"].lower()!=target.suffix[1:].lower(): raise HancomError("format must match output extension")
    if target==source and not job.get("overwrite_source",False): raise HancomError("Writing over input requires overwrite_source:true")
    if target!=source and target.exists() and not job.get("overwrite_output",False): raise HancomError("Output exists; choose another path or overwrite_output:true")
    verify=job.get("verify",[])
    if not isinstance(verify,list): raise HancomError("verify must be an array")
    staged=_stage(target)
    try:
        with HancomBridge(visible=job.get("visible",False)) as bridge:
            if not bridge.security_module_registered and not bridge.visible: raise HancomError("Hancom file access module is not registered; run doctor and configure FilePathCheckDLL, or use visible:true and handle the Hancom prompt interactively")
            if source: bridge.open_doc(source)
            operations(bridge,job.get("operations",[]))
            result=bridge.save_verified(staged)
            result["verification_results"]=[assertion(bridge,spec) for spec in verify]
            result["requested_effects_verified"]=bool(verify)
        if source and _sha(source)!=source_hash: raise HancomError("Input changed while processing")
        if target!=source and target.exists() and not job.get("overwrite_output",False): raise HancomError("Output appeared while processing")
        os.replace(staged,target)
        return {"schema":1,"action":action,"success":True,"format":target.suffix[1:].lower(),"preservation":"hancom_runtime_save",
                "artifacts":{"document":str(target)},"input_sha256":source_hash,"output_sha256":_sha(target),"zero_difference_verified":False,**result}
    finally: staged.unlink(missing_ok=True)
