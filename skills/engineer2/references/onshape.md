# Onshape: the FeatureScript write path

The rule: **the canonical FeatureScript lives in a Feature Studio inside the same Onshape document
as the model.** That is what makes it reviewable by a human in the CAD tool. engineer2.me stores a
hashed mirror so the run record is self-contained and diffable, but the document is the source.

## The loop

1. **Find or create the model item.** A `cad_model` records `did` / `wid` / `eid` and a pin.
   Pin to a version (`{"kind": "version", "vid": "…"}`) once an evaluation is verified — a
   workspace-pinned model can move under a passing evaluation, which shows up as drift.

2. **Author the FeatureScript.** Use the FeatureScript MCP server (or the Onshape UI) to write and
   insert it into a Feature Studio in that same document. Iterate there until it evaluates cleanly —
   it is much faster to debug in Onshape than through a verification round-trip.

   A geometry-metrics lambda looks like this:

   ```
   function(context is Context, queries) {
       var body = qBodyType(qEverything(EntityType.BODY), BodyType.SOLID);
       var props = evaluateBoundingBox(context, body);
       return {
           "mass_kg": massProperties(context, body).mass[0] / kilogram,
           "bbox_x_mm": (props.maxCorner[0] - props.minCorner[0]) / millimeter
       };
   }
   ```

   Return plain numbers, strings, maps, arrays, or values with units. The platform decodes
   `BTFSValueMap`, `BTFSValueArray`, numbers, strings, booleans, and `BTFSValueWithUnits`
   (→ `{"value": …, "units": "kg"}`). Anything else is passed through untouched and marks the run
   as `error` rather than being guessed at.

3. **Divide by units deliberately.** `x / millimeter` yields a bare number in millimetres; leaving
   the units on yields `{"value": …, "units": "mm"}`. Either is fine — but `expect` must match the
   shape you return, and unit strings must match exactly. There is no automatic conversion.

4. **Register the evaluation** on the platform:

   ```python
   api.post(
       "/items",
       json={
           "kind": "cad_evaluation",
           "title": "Arm cross-section metrics",
           "payload": {
               "model": model_id,
               "script": open("eval.fs").read(),
               "onshape_source": {"eid": fs_element_id, "element_type": "FEATURESTUDIO", "name": "ArmMetrics"},
               "expect": {"mass_kg": 0.0431, "bbox_x_mm": 250.0},
               "tolerance": {"rel": 1e-3},
           },
       },
   )
   ```

   `evaluates` is added for you. `POST /items/{id}/verify?wait=60` runs it against Onshape.

5. **Chain it.** The evaluation's output is `$ref`-able as a calculation input:

   ```json
   {"I_mm4": {"$ref": "item:<eval-uuid>#/payload/expect/second_moment_mm4"}}
   ```

   Prefer referencing the *evaluation* over hard-coding geometry: when the model changes, the chain
   goes stale visibly instead of silently staying wrong.

## Drift

Every run records the `sourceMicroversion`. Verifying a `cad_model` is a reachability probe that
records it too; if it moved since the last probe, the platform demotes every `verified` evaluation
of that model to `proposed`. That is not a claim they are wrong — only that nobody has re-checked
them against the geometry as it now stands.

## Credentials

`ONSHAPE_ACCESS_KEY` / `ONSHAPE_SECRET_KEY`, used as HTTP Basic against
`https://cad.onshape.com/api/v9`. Without them CAD items still store and render; verification
returns a "credentials not configured" error and the UI shows a banner.
