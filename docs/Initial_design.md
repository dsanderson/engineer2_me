# engineer2.me

This is a repo inspired by prove2.me for engineering design, and specifically mechanical engineering, work.  First off, make sure you understand how prove2.me works: the starting point is here: `https://prove2.me/start.md`, which eventually points to a github repo.  The core conceit is that it serves as a platform for agents to maintain human-readable *and Lean-verified* math proofs: those proofs can be chained together into "missions", more complex proofs.  Missions and the *theorem to prove* are often written by humans, though agents also often write those theorems.  While agents generally verify their own lean proofs, the platform backstops those proofs.  This provides a way for agents to coordinate: they can check what proofs for a mission have been solved, which need solving, which can be discarded or are flaged as depricated or not worth persuing, and can propose new, small intermediate proofs as they go for other agents to pick up.

## Translating to engineering

Engineering does not have the same elegant provability as pure math.  However, we have a similar pattern of chaining together a network of small facts/proofs in the form of requirements, and we have several existing tools for veryfying/validating those requirements.  engineer2.me is a platform like prove2.me, but focused on less rigor for a subset of engineering design.  Off the top of my head, we support the following 3 "statments":
- A "fact", a JSONable data structure, and one or more source documents (arbitrary files) that contain the proof.  This is used, for example, for an agent to store spec sheets, requirements, or facts about say materials.  This is important to provide a time-and-date stamped record of key data, that is identified as AI populated.  Note that in this framework, it would start as a "question" in human-readable form, then get populated by an agent or person doing research.
- A "calculator": a python script, run in a container we provide the definition for with basic numberic & scientific python packages.  This provides a 
- A "calculation": a reference to a calculator, and the inputs, all on engineer2.me, as well as the expected output.  The inputs should be some easily-fetched-and-loaded reference on our platform, probably via GET (httpx) and loaded via JSON.
- A "CAD model": a reference to an Onshape document, workspace and element (as a clickable link): we use this as the basis for geometric calculations and eventually simulation.
  - Note: in the short term, we'll have a utility wrapper/skill for agents that lets them use the featurescript MCP skill to generate geometry and calculations on the model.  The featurescript should be stored in the same document: the skill can provbide a short python script for automatically "applying" the featurescript to a partstudio.  By chaining this together, it can also extract facts from the geometry
- A "CAD evaluation": Following the pattern above, the results of a featurescript run in a target partstudio: this allows us to evaluate properties of a cad model, probably using the lambda endpoint (for now)
- An "idea": unstructured text that can be used to store a "mission" equivalent or intermediate goals: for example, an "IR camera" may not fit as a "fact" but may be a path worth exploring; we need a way of referencing it.  Probably markdown

## The Platform

engineer2.me allows us to store, reference and, when relevant, depricate (but not delete) any of the above.  That way, agents can pick up a mission or item and work on it, also reviewing the state of the mission so far.  That means each of the above must have a state, and the ability to reference any other items, perhaps with some metadata to explain the reference.   The platform itself can verify calculations and CAD evaluations by 

## The Design

To start, this is a research prototype.  We don't need to handle volume, or security.  For calculations, we can probably just use `eval` inside a container.  For the items, we can plan to store them in files/folders on the filesystem.  If we generate a UUID for each, that can just be the folder name; we can have a small heirarchy of folder prefixes if needed.  We'll run the platform in docker, behind caddy.  Make it easy to run locally or on a server, loading https certs if we can, and we can probably just use http basic auth for now (make it easy for requests or httpx to hit it).  Just mount some storage folder to the container for the data storage.

Use fasthttp or fasthtml for the server.  I would prefer simple static pages templated and served by fasthttp.

Make sure we can create or navigate missions and individual items, including seeing the references beteween them.

Keep the code as simple and readable as possible.