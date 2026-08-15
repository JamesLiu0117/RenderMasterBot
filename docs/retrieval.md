# Chroma asset retrieval

The retrieval layer converts validated `AssetCard` records into multilingual
documents, embeds them with Ollama, and stores the vectors in a persistent
Chroma collection using cosine distance.

## Why the embedding model is separate

`gpt-oss:20b` and `qwen3-vl:8b-instruct` are generation and vision models. Asset search
uses `qwen3-embedding:0.6b`, a much smaller dedicated embedding model that can
match Chinese requests with English Unreal asset names and paths. The model is
run locally through Ollama; Chroma never downloads a hidden default model.

## Index identity and synchronization

Collection metadata records the AssetCard schema, retrieval-document format,
and exact embedding model. Opening an existing collection with a different
embedding model fails explicitly, preventing mixed vector dimensions or silent
quality drift.

`asset-index` is a synchronization operation:

1. Validate every JSON item as an `AssetCard`.
2. Create stable multilingual retrieval text.
3. Generate one vector per card through `/api/embed`.
4. Upsert current cards into Chroma.
5. Delete IDs that are no longer present in the source catalog.

Re-running the same catalog updates existing records instead of creating
duplicates. The first complete OptimizationPlugin synchronization indexed 539
records through `qwen3-embedding:0.6b`.

## Planner constraint

`plan --retrieve-assets N` searches with the user's prompt, supplies the ranked
asset metadata to the planner, and constructs an exact allowed-ID set.
`--retrieve-materials N` adds a second query restricted to assignable material
cards, preventing meshes and Blueprints from crowding material candidates out
of the combined context. After the model returns JSON, RenderMasterBot rejects
the plan if any primary mesh or material assignment refers to an ID outside
that set. Retrieval therefore narrows model choice without granting the model
direct database or Unreal access.

`asset-search --asset-type material` exposes the same strict metadata filter for
manual inspection. Type filtering narrows the candidates but does not prove
visual suitability. The planner is instructed to require supporting names,
descriptions, or tags and to record a missing material instead of treating the
nearest vector as a valid substitute.

```powershell
render-master asset-search `
  --query "natural oak wood grain" `
  --asset-type material `
  --limit 5

render-master plan `
  --prompt "Create a wooden door product shot" `
  --retrieve-assets 8 `
  --retrieve-materials 5
```

Use several candidates for requests containing multiple intentions. For
example, a prompt mentioning both a first-person level and a door can retrieve
both level and door records; the planner then chooses among only those bounded
candidates.

## First material-retrieval baselines

A real `gpt-oss:20b` plan using general plus material-only retrieval correctly
assigned `M_PrototypeGrid` to `SM_Door.Material_0` when that exact technical
test was requested. The validated plan used only retrieved IDs.

A second plan requested a natural oak wood-grain door. The complete 539-card
project catalog contained 28 assignable materials but no wood evidence. The
material-only nearest neighbors were unrelated prototype and character
materials with low similarity. The planner selected the real door mesh, left
its materials list empty, and recorded `Missing oak wood-grain material asset`
instead of substituting an unrelated nearest neighbor.

## Local configuration

The relevant environment variables are:

```text
RENDERMASTER_EMBEDDING_MODEL=qwen3-embedding:0.6b
RENDERMASTER_CHROMA_DIR=<local data root>\chroma
RENDERMASTER_ASSET_COLLECTION=render_master_assets_v01
```

Model weights and Chroma files are runtime data and must remain outside the Git
source repository.
