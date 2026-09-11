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
       var bb = evBox3d(context, { "topology" : body });
       return {
           "mass_kg": evMassProperties(context, { "entities" : body }).mass[0] / kilogram,
           "bbox_x_mm": (bb.maxCorner[0] - bb.minCorner[0]) / millimeter
       };
   }
   ```

   The evaluation functions are the `ev*` ones — `evBox3d`, `evVolume`, `evMassProperties`, `evArea`
   — and each takes a **map**, not positional arguments. Getting either wrong returns
   `Function … with 2 argument(s) not found` as a *warning* and a `null` result, which reads as an
   empty model rather than as a typo.

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

## Making geometry: inserting a custom feature

The loop above *reads* geometry. To *create* it, a Feature Studio is not enough: writing one only
defines a feature, and nothing appears in the Part Studio until an instance of that feature is added
to its feature list. `scripts/insert_feature.py` does that second half, so a mission can go from a
`.fs` file to a real solid with nobody clicking anything.

```bash
# what does this document define, and what parameters does each feature take?
python scripts/insert_feature.py --url "$PS_URL" --list

# push the .fs and instantiate it, in one call
python scripts/insert_feature.py --url "$PS_URL" \
    --file cube.fs --studio CubeFeature \
    --feature-type agentCube --name "Base cube" --param side="50 mm"
```

It appends to the **end** of the feature list, prints the new `featureId` as JSON, and exits
non-zero if the feature lands with a non-OK status — so a feature that regenerates with an error
cannot be mistaken for working geometry. Run `--list` first: it is the cheapest way to learn the
exact `featureType` and parameter ids, and an empty list is the signal that the studio did not
compile.

Parameters are typed from the feature's own spec, so you do not have to know Onshape's wire types:
a length takes an expression string (`side="50 mm"`, or `side="#width * 2"` to reference a
variable), a boolean takes `true`/`false`, and a query or array parameter takes JSON via
`--param id:='[...]'`. Naming a parameter the feature does not declare is an error rather than a
silent no-op — a mistyped parameter id would otherwise leave the feature silently at its default,
which is the CAD equivalent of the metre/millimetre swap.

`--if-absent` makes a re-run a no-op when a feature of that name is already in the list, which is
what you want when a mission step is retried. There is no delete path; remove a feature in the UI.

Three things that are easy to get wrong:

* **Do not name a feature after a std one.** `cube`, `sphere`, `extrude` and friends are already
  exported by `onshape/std/geometry.fs`; redefining one makes the *whole Feature Studio* fail to
  compile, and the only symptom is that `--list` reports no features at all.
* **The namespace embeds a microversion** (`e<studioId>::m<microversion>`), so it has to be read at
  insert time. The script always re-reads it; if you build the call yourself, resolving a namespace
  once and reusing it later pins the instance to a stale definition of the feature.
* **The Feature Studio contents endpoint is the bare element path**, `POST
  /featurestudios/d/{did}/w/{wid}/e/{eid}` with `{"contents": …}`. A `/contents` suffix 404s.

The current FeatureScript version — the number in the `FeatureScript 3070;` header and the matching
`import` — comes back as `libraryVersion` on any FeatureScript evaluation, so an agent can read it
rather than guess.

## Drift

Every run records the `sourceMicroversion`. Verifying a `cad_model` is a reachability probe that
records it too; if it moved since the last probe, the platform demotes every `verified` evaluation
of that model to `proposed`. That is not a claim they are wrong — only that nobody has re-checked
them against the geometry as it now stands.

## Credentials

`ONSHAPE_ACCESS_KEY` / `ONSHAPE_SECRET_KEY`, used as HTTP Basic against
`https://cad.onshape.com/api/v9`. Without them CAD items still store and render; verification
returns a "credentials not configured" error and the UI shows a banner.
