# AiZynthFinder Skill

Use this skill when an agent needs to plan or evaluate a **synthesis route
for a drug molecule** — retrosynthetic planning with the AiZynthFinder
engine (MolecularAI/aizynthfinder, MIT) exposed through the MCP server in
`pharmacy-mcp/` (`server.py`, stdio transport).

## When to use it

- A target molecule (SMILES) is given and the question is "can it be made,
  and how?" — plan routes, check whether they are solved, list building
  blocks.
- A route JSON (from `plan_retrosynthesis` or `aizynthcli`) needs a plain
  summary: steps, solved status, building blocks in/out of stock.
- A SMILES string needs validation before any chemistry work.
- A common drug name needs to be turned into a SMILES to start planning.

## When NOT to use it

- Drug information questions (dose, interactions, indications, side
  effects): this server does not answer those. Use a drug-information
  source instead.
- Iranian availability, Iranian brands, doses or prices: this server
  does not know them. Never take them from the sample table.
- Judging whether a proposed route is safe or practical in a real lab:
  that needs a human organic chemist. The server only reports what the
  model found.

## Server setup

```bash
cd pharmacy-mcp
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python server.py --check   # list tools + engine status
```

MCP client config (stdio):

```json
{
  "mcpServers": {
    "aizynthfinder-pharmacy": {
      "command": "/absolute/path/to/pharmacy-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/pharmacy-mcp/server.py"],
      "env": {
        "AIZYNTH_CONFIG": "/absolute/path/to/my_folder/config.yml"
      }
    }
  }
}
```

`AIZYNTH_CONFIG` is optional. Without models installed, the tools that
do not need models still work, and `plan_retrosynthesis` returns a
structured `model_not_configured` error.

## Full retrosynthesis runs need model files

A real run needs an AiZynthFinder config file pointing at:

1. an expansion policy model (ONNX),
2. reaction templates,
3. a stock file (e.g. ZINC),
4. optionally a filter policy model.

Get them with the upstream helper (large downloads):

```bash
download_public_data my_folder   # creates my_folder/config.yml
```

Then pass that file as `config_path` or via `AIZYNTH_CONFIG`. The
server never downloads model files by itself. If a run is impossible,
`plan_retrosynthesis` says so explicitly instead of failing silently.

## Tools

### `aizynth_status(config_path?)`
Call FIRST before any run. Reports whether `rdkit`, `aizynthfinder`,
`onnxruntime`/`tensorflow` are importable, which config file was found,
which files it references exist, and `ready_for_retrosynthesis`.

Example call:

```json
{"config_path": "/path/to/my_folder/config.yml"}
```

### `validate_smiles(smiles)`
Validates a SMILES string. With RDKit: sanitisation, canonical SMILES,
formula, molecular weight. Without RDKit: a labelled heuristic check
only — do not treat that as a real validation.

Example call:

```json
{"smiles": "CC(=O)NC1=CC=C(C=C1)O"}
```

Example result (with RDKit):

```json
{"valid": true, "method": "rdkit", "canonical_smiles": "CC(=O)Nc1ccc(O)cc1",
 "formula": "C8H9NO2", "molecular_weight": 151.16}
```

### `lookup_drug_smiles(drug_name?)`
Small **sample/unverified** table of well-known generic molecules
(paracetamol, aspirin, ibuprofen, metformin, amoxicillin) mapping a
generic name to a SMILES. Call with no argument to list all entries.

Example call:

```json
{"drug_name": "metformin"}
```

### `plan_retrosynthesis(smiles, config_path?, max_routes?, iteration_limit?, time_limit?)`
Runs the MCTS search and returns the top routes as AiZynthFinder route
dicts (JSON) plus statistics. Defaults: `max_routes=5`,
`iteration_limit=100`, `time_limit=120` (seconds). Keep limits modest
for interactive use; large molecules can take minutes.

Example call:

```json
{"smiles": "CC(=O)NC1=CC=C(C=C1)O", "max_routes": 5, "time_limit": 120}
```

Example result when models are missing (expected without setup):

```json
{"status": "model_not_configured",
 "error": "AiZynthFinder is not ready for a retrosynthesis run on this machine — no model files were downloaded.",
 "how_to_fix": "Download the public policy/stock data with ..."}
```

On success, `status` is `"ok"` and `routes` holds the route dicts;
`solved_routes` counts routes whose leaf building blocks are all in
stock. Feed a route dict to `summarize_route` next.

### `summarize_route(route_json)`
Summarises one route dict (JSON string). Needs no model files.
Returns solved status, number of reaction steps, tree depth, and the
leaf building blocks with in-stock flags, plus a one-line `summary`.

Example call:

```json
{"route_json": "<route dict JSON from plan_retrosynthesis>"}
```

A test fixture is available at `pharmacy-mcp/sample_route.json`
(hand-built illustrative route for paracetamol — not a real model
output) for exercising this tool without models.

## Typical workflow

1. `aizynth_status` — is the engine ready?
2. `lookup_drug_smiles` (or a trusted structure source such as PubChem)
   → SMILES for the drug.
3. `validate_smiles` — confirm the SMILES parses.
4. `plan_retrosynthesis` — get routes (or a clear `model_not_configured`
   error with fix instructions).
5. `summarize_route` per route — solved?, steps, building blocks.
6. Report: best solved route first; list building blocks and flag any
   that are not in stock; never claim a route is lab-ready.

## Limits — read before reporting results

- "Solved" means only that every leaf molecule is in the configured
  stock catalogue (e.g. ZINC). It does NOT mean available in Iran,
  affordable, or of pharmaceutical grade.
- The policy model was trained on USPTO reactions; rare, recent, or
  lab-specific reactions may be missed, and proposed reactions still
  need a chemist's review (conditions, yield, safety, purity).
- The sample drug table is **sample/unverified**: it holds no Iranian
  brands, availability, doses or prices. For anything about the
  Iranian market, verify against the user's own sources — the
  Payamdaroosaz website and the IFDA yearbook — and say so in the
  answer. Never invent a brand, a dose or a price.
- Do not download large model files without the user's approval; the
  server degrades gracefully without them.
