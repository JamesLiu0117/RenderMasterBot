# Chroma asset retrieval

The retrieval layer converts validated `AssetCard` records into multilingual
documents, embeds them with Ollama, and stores the vectors in a persistent
Chroma collection using cosine distance.

## Why the embedding model is separate

`gpt-oss:20b` and `qwen3.5:9b` are generation and vision models. Asset search
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

Re-running the same catalog updates the 20 existing records instead of creating
duplicates.

## Planner constraint

`plan --retrieve-assets N` searches with the user's prompt, supplies the ranked
asset metadata to the planner, and constructs an exact allowed-ID set. After the
model returns JSON, RenderMasterBot rejects the plan if any scene object refers
to an ID outside that set. Retrieval therefore narrows model choice without
granting the model direct database or Unreal access.

Use several candidates for requests containing multiple intentions. For
example, a prompt mentioning both a first-person level and a door can retrieve
both level and door records; the planner then chooses among only those bounded
candidates.

## Local configuration

The relevant environment variables are:

```text
RENDERMASTER_EMBEDDING_MODEL=qwen3-embedding:0.6b
RENDERMASTER_CHROMA_DIR=<local data root>\chroma
RENDERMASTER_ASSET_COLLECTION=render_master_assets_v01
```

Model weights and Chroma files are runtime data and must remain outside the Git
source repository.
