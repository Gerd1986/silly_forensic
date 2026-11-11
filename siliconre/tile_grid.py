    target=Path(output)
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_name(target.stem+".partial"+target.suffix)
    try:
        result={"method":"filename_tile_grid","row_direction":"increasing_down",
                "column_direction":"increasing_right","bounds":plan["bounds"],
                "tile_size":list(plan["tile_size"]),"size":plan["size"],
                "missing_tiles":[list(rc) for rc in plan["missing"]],
                "sources":[{"row":r,"column":c,"path":str(path)}
                           for (r,c),path in sorted(plan["tiles"].items())],
                "output":str(target)}
        target.with_suffix(".sources.json").write_text(
            json.dumps(result,indent=2),encoding="utf-8")
        return result
    finally:
        if temp.exists():
            temp.unlink()
