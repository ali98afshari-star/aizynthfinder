# AiZynthFinder Pharmacy MCP Server

An MCP (Model Context Protocol) server that exposes the AiZynthFinder
retrosynthesis engine to AI agents, tailored to pharmacy work: plan a
synthesis route for a drug molecule, summarise/score routes, list building
blocks, and validate SMILES. Transport is **stdio**.

Upstream project: [MolecularAI/aizynthfinder](https://github.com/MolecularAI/aizynthfinder)
(MIT licence, Copyright 2020 Samuel Genheden and Esben Bjerrum).
This server is an addition built on top of it; the original `LICENSE`
file is kept untouched in the repo root.

## Tools

| Tool | What it does | Needs models? |
|---|---|---|
| `aizynth_status` | Checks whether the engine and model/stock files are ready; lists missing files | No |
| `validate_smiles` | Validates a SMILES (RDKit sanitisation if installed, else a labelled heuristic check) | No |
| `lookup_drug_smiles` | Sample generic-name → SMILES table (a few common drugs) | No |
| `plan_retrosynthesis` | Runs the MCTS retrosynthesis search, returns top routes as JSON | **Yes** |
| `summarize_route` | Summarises one route JSON: solved?, steps, building blocks, in-stock status | No |

## Install

```bash
cd pharmacy-mcp
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

For a real retrosynthesis run you additionally need the engine and its
data files (large downloads — the server never downloads them itself):

```bash
# engine (from the repo root, editable install)
pip install -e .[all]
# or: pip install aizynthfinder[all]

# public policy + stock data (creates my_folder/config.yml)
download_public_data my_folder
```

Then point the server at the config file with the `config_path`
argument of `plan_retrosynthesis` / `aizynth_status`, or set
`AIZYNTH_CONFIG=/path/to/my_folder/config.yml`.

## Run

```bash
# stdio server (for MCP clients such as Claude Desktop, MCP Inspector)
.venv/bin/python server.py

# quick self-check: list tools + engine status, then exit
.venv/bin/python server.py --check
```

Example MCP client configuration (stdio):

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

Omit `AIZYNTH_CONFIG` if no models are installed — the tools that do
not need models still work, and `plan_retrosynthesis` will return a
clear `model_not_configured` error instead of failing.

## Behaviour without models

If the policy/stock files are missing, `plan_retrosynthesis` returns:

```json
{
  "status": "model_not_configured",
  "error": "AiZynthFinder is not ready for a retrosynthesis run on this machine — no model files were downloaded.",
  "missing_files": ["..."],
  "how_to_fix": "Download the public policy/stock data with ..."
}
```

No large files are ever downloaded automatically.

## Iranian-market data — read this

`lookup_drug_smiles` is a **small sample table only** (a few well-known
generic molecules, name → SMILES), clearly labelled sample/unverified.
It contains **no Iranian brand names, no availability, no doses and no
prices** — none are invented here. An agent using this server must verify
Iranian availability, brands, doses and prices only against the user's
own sources (Payamdaroosaz website / IFDA yearbook), never from this
sample.

## Test fixture

`sample_route.json` is a small hand-built route in AiZynthFinder's route
dict format (paracetamol from 4-aminophenol + acetic anhydride,
illustrative only — not a real model output). Use it to exercise
`summarize_route` without any model files.

## Licence

MIT — see `../LICENSE` (upstream AiZynthFinder licence).
