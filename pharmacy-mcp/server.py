"""AiZynthFinder pharmacy MCP server (stdio transport).

A small Model Context Protocol server that exposes the AiZynthFinder
retrosynthesis engine (MolecularAI/aizynthfinder, MIT) to AI agents,
tailored to pharmacy work: plan a synthesis route for a drug molecule,
score/summarise routes, check building blocks, and validate SMILES.

Design notes
------------
* The heavy engine (RDKit, AiZynthFinder, TensorFlow/ONNX policy models)
  is imported LAZILY, only inside the tool that needs it. The server
  starts and lists its tools even on a machine where nothing is installed.
* A full retrosynthesis run needs model + stock files (see
  ``aizynth_status``). If they are missing, ``plan_retrosynthesis``
  does NOT download anything; it returns a structured error telling
  the caller exactly what is missing and how to get it
  (``download_public_data`` from the AiZynthFinder docs).
* No Iranian brand names, doses or prices are invented anywhere here.
  ``lookup_drug_smiles`` contains only a small SAMPLE of well-known
  generic molecules (name -> SMILES), clearly labelled sample/unverified.
  Iranian availability, brands, doses and prices must always be verified
  against the user's own sources (Payamdaroosaz website / IFDA yearbook),
  never taken from this sample.
* Only this file's own code is in English (as is the whole repo);
  no code, comments or UI strings are translated to Persian.

Run:
    python server.py            # stdio transport (for MCP clients)
    python server.py --check    # print tool list + status, then exit
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Make the AiZynthFinder package importable when this server lives inside
# a clone of the repo (server is at <repo>/pharmacy-mcp/server.py).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("aizynthfinder-pharmacy")

# ---------------------------------------------------------------------------
# Small SAMPLE table: generic drug name -> SMILES.
# SAMPLE / UNVERIFIED. These are widely published structures of common
# molecules, included only so an agent can go from a drug name to a SMILES
# string to feed into retrosynthesis planning. They say NOTHING about
# Iranian brands, availability, dose or price. Verify all of that against
# the user's own sources (Payamdaroosaz / IFDA yearbook).
# ---------------------------------------------------------------------------
SAMPLE_DRUGS = {
    "paracetamol": {
        "generic_name": "Paracetamol (acetaminophen)",
        "smiles": "CC(=O)NC1=CC=C(C=C1)O",
        "note": "Analgesic/antipyretic. SAMPLE entry, unverified.",
    },
    "acetaminophen": {
        "generic_name": "Paracetamol (acetaminophen)",
        "smiles": "CC(=O)NC1=CC=C(C=C1)O",
        "note": "Same molecule as paracetamol. SAMPLE entry, unverified.",
    },
    "aspirin": {
        "generic_name": "Aspirin (acetylsalicylic acid)",
        "smiles": "CC(=O)OC1=CC=CC=C1C(=O)O",
        "note": "NSAID / antiplatelet. SAMPLE entry, unverified.",
    },
    "ibuprofen": {
        "generic_name": "Ibuprofen",
        "smiles": "CC(C)CC1=CC=C(C=C1)C(C)C(=O)O",
        "note": "NSAID. SAMPLE entry, unverified.",
    },
    "metformin": {
        "generic_name": "Metformin",
        "smiles": "CN(C)C(=N)NC(=N)N",
        "note": "Antidiabetic (biguanide). SAMPLE entry, unverified.",
    },
    "amoxicillin": {
        "generic_name": "Amoxicillin",
        "smiles": "CC1(C)S[C@@H]2[C@H](NC(=O)[C@@H](N)C3=CC=C(O)C=C3)C(=O)N2[C@H]1C(O)=O",
        "note": "Penicillin antibiotic. SAMPLE entry, unverified.",
    },
}

DEFAULT_CONFIG_NAMES = ("config.yml", "config.yaml", "config_local.yml")

HOW_TO_GET_MODELS = (
    "Download the public policy/stock data with the AiZynthFinder helper "
    "(needs internet, downloads are large): "
    "`download_public_data my_folder` — this creates a config.yml in "
    "my_folder. Then pass that config file as config_path, or set the "
    "AIZYNTH_CONFIG environment variable to its path. "
    "Docs: https://molecularai.github.io/aizynthfinder/"
)


def _find_config(explicit: str | None) -> str | None:
    """Resolve the AiZynthFinder config file path, or None if not found."""
    candidates = []
    if explicit:
        candidates.append(explicit)
    env_path = os.environ.get("AIZYNTH_CONFIG")
    if env_path:
        candidates.append(env_path)
    for name in DEFAULT_CONFIG_NAMES:
        candidates.append(os.path.join(os.getcwd(), name))
        candidates.append(os.path.join(REPO_ROOT, name))
    for path in candidates:
        if path and os.path.isfile(path):
            return os.path.abspath(path)
    return None


def _parse_config_paths(config_path: str) -> dict:
    """Best-effort extraction of file paths referenced by a config file."""
    info: dict = {"config_path": config_path, "parsed": False, "files": {}}
    try:
        import yaml  # type: ignore
    except ImportError:
        info["error"] = "pyyaml is not installed, cannot parse config file"
        return info
    try:
        with open(config_path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except Exception as exc:  # noqa: BLE001 - report, don't crash
        info["error"] = f"cannot read config file: {exc}"
        return info
    if not isinstance(raw, dict):
        info["error"] = "config file is not a YAML mapping"
        return info
    info["parsed"] = True

    def _collect(section: object, out: dict) -> None:
        if isinstance(section, dict):
            for key, value in section.items():
                if isinstance(value, str):
                    out[str(key)] = value
                elif isinstance(value, (list, tuple)):
                    for i, item in enumerate(value):
                        if isinstance(item, str):
                            out[f"{key}[{i}]"] = item
                elif isinstance(value, dict):
                    _collect(value, out)

    files: dict = {}
    for section_name in ("expansion", "filter", "stock"):
        _collect(raw.get(section_name), files)
    info["files"] = {
        key: {"path": path, "exists": os.path.isfile(path)}
        for key, path in files.items()
    }
    return info


def _engine_availability() -> dict:
    """Check which parts of the AiZynthFinder engine are importable."""
    out: dict = {}
    for module in ("rdkit", "onnxruntime", "tensorflow"):
        try:
            __import__(module)
            out[module] = True
        except ImportError:
            out[module] = False
    # For aizynthfinder, importing the top-level package alone is not
    # enough (it has no heavy imports); check the real entry point.
    try:
        from aizynthfinder.aizynthfinder import AiZynthFinder  # noqa: F401

        out["aizynthfinder"] = True
    except ImportError:
        out["aizynthfinder"] = False
    return out


def _basic_smiles_check(smiles: str) -> dict:
    """Heuristic SMILES sanity check used when RDKit is unavailable."""
    problems = []
    if not smiles or not smiles.strip():
        problems.append("empty SMILES")
    if smiles.count("(") != smiles.count(")"):
        problems.append("unbalanced parentheses")
    if smiles.count("[") != smiles.count("]"):
        problems.append("unbalanced brackets")
    allowed = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        "()[]=#+-\\/%.@:*~"
    )
    bad = sorted({ch for ch in smiles if ch not in allowed})
    if bad:
        problems.append(f"unexpected characters: {''.join(bad)}")
    return {"valid": not problems, "problems": problems}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool()
def aizynth_status(config_path: str | None = None) -> dict:
    """Check whether the AiZynthFinder engine and its model/stock files are
    ready for a retrosynthesis run.

    Returns import status of rdkit/aizynthfinder/onnxruntime/tensorflow,
    the resolved config file (if any) and whether every file it references
    exists on disk. Call this before plan_retrosynthesis.
    """
    config = _find_config(config_path)
    result: dict = {
        "engine": _engine_availability(),
        "config_path": config,
        "ready_for_retrosynthesis": False,
        "how_to_get_models": HOW_TO_GET_MODELS,
    }
    if config is None:
        result["config_error"] = (
            "No AiZynthFinder config file found. Pass config_path, set "
            "AIZYNTH_CONFIG, or place a config.yml in the working directory."
        )
        return result
    parsed = _parse_config_paths(config)
    result["config"] = parsed
    files = parsed.get("files", {})
    missing = [k for k, v in files.items() if not v.get("exists")]
    result["missing_files"] = missing
    result["ready_for_retrosynthesis"] = bool(
        result["engine"].get("aizynthfinder")
        and result["engine"].get("rdkit")
        and parsed.get("parsed")
        and not missing
    )
    return result


@mcp.tool()
def validate_smiles(smiles: str) -> dict:
    """Validate a SMILES string.

    With RDKit installed: full sanitisation, canonical SMILES, molecular
    formula and molecular weight. Without RDKit: a clearly-labelled
    heuristic check only (balanced brackets, allowed characters).
    """
    smiles = (smiles or "").strip()
    try:
        from rdkit import Chem  # type: ignore
        from rdkit.Chem import Descriptors, rdMolDescriptors  # type: ignore
    except ImportError:
        check = _basic_smiles_check(smiles)
        return {
            "smiles": smiles,
            "valid": check["valid"],
            "method": "heuristic (RDKit NOT installed — install rdkit for a real check)",
            "problems": check["problems"],
        }
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return {
            "smiles": smiles,
            "valid": False,
            "method": "rdkit",
            "problems": ["RDKit could not parse this SMILES"],
        }
    try:
        Chem.SanitizeMol(mol)
    except Exception as exc:  # noqa: BLE001
        return {
            "smiles": smiles,
            "valid": False,
            "method": "rdkit",
            "problems": [f"sanitisation failed: {exc}"],
        }
    return {
        "smiles": smiles,
        "valid": True,
        "method": "rdkit",
        "canonical_smiles": Chem.MolToSmiles(mol),
        "formula": rdMolDescriptors.CalcMolFormula(mol),
        "molecular_weight": round(Descriptors.MolWt(mol), 2),
        "problems": [],
    }


@mcp.tool()
def lookup_drug_smiles(drug_name: str | None = None) -> dict:
    """Look up the SMILES of a common drug by generic name.

    SAMPLE / UNVERIFIED table of a few well-known molecules, only to turn a
    drug name into a SMILES string for retrosynthesis planning. It contains
    NO Iranian brands, NO availability, NO doses and NO prices. Verify
    Iranian availability/brands/doses/prices only against the user's own
    sources (Payamdaroosaz website / IFDA yearbook).

    Call with no argument (or empty string) to list all sample entries.
    """
    if not drug_name or not drug_name.strip():
        return {
            "sample_table": True,
            "verified": False,
            "warning": (
                "SAMPLE table only — unverified. No Iranian brands, "
                "availability, doses or prices are included. Verify "
                "everything about the Iranian market against Payamdaroosaz "
                "or the IFDA yearbook."
            ),
            "entries": SAMPLE_DRUGS,
        }
    key = drug_name.strip().lower()
    entry = SAMPLE_DRUGS.get(key)
    if entry is None:
        return {
            "found": False,
            "drug_name": drug_name,
            "sample_table": True,
            "verified": False,
            "message": (
                f"'{drug_name}' is not in the small sample table. This "
                "table is only a sample — get the SMILES from a trusted "
                "structure source (e.g. PubChem) instead of guessing."
            ),
            "available_sample_names": sorted(SAMPLE_DRUGS),
        }
    return {"found": True, "sample_table": True, "verified": False, **entry}


@mcp.tool()
def plan_retrosynthesis(
    smiles: str,
    config_path: str | None = None,
    max_routes: int = 5,
    iteration_limit: int = 100,
    time_limit: int = 120,
) -> dict:
    """Plan retrosynthesis routes for a target molecule (SMILES).

    Runs AiZynthFinder's Monte Carlo tree search with the configured
    expansion policy and stock, and returns the top routes as
    AiZynthFinder route dicts (JSON) plus search statistics.

    If the engine or the model/stock files are missing, NOTHING is
    downloaded. Instead a structured error is returned
    (status == "model_not_configured") explaining what is missing and
    how to get it. The run can take minutes for large molecules; keep
    time_limit modest for interactive use.
    """
    smiles = (smiles or "").strip()
    if not smiles:
        return {"status": "error", "error": "smiles must not be empty"}
    if max_routes < 1:
        max_routes = 1
    if max_routes > 25:
        max_routes = 25

    status = aizynth_status(config_path)
    if not status["ready_for_retrosynthesis"]:
        missing = status.get("missing_files") or []
        not_installed = [
            mod for mod, ok in status["engine"].items() if not ok
        ]
        return {
            "status": "model_not_configured",
            "error": (
                "AiZynthFinder is not ready for a retrosynthesis run on "
                "this machine — no model files were downloaded."
            ),
            "config_path": status.get("config_path"),
            "config_error": status.get("config_error"),
            "missing_files": missing,
            "not_installed": not_installed,
            "how_to_fix": HOW_TO_GET_MODELS,
            "hint": "Call aizynth_status first; then download the public "
            "data and pass the generated config.yml as config_path.",
        }

    try:
        from aizynthfinder.aizynthfinder import AiZynthFinder  # type: ignore
    except ImportError as exc:
        return {
            "status": "error",
            "error": f"aizynthfinder package could not be imported: {exc}",
        }

    try:
        finder = AiZynthFinder(configfile=status["config_path"])
        finder.target_smiles = smiles
        finder.config.search.iteration_limit = iteration_limit
        finder.config.search.time_limit = time_limit
        finder.config.post_processing.max_routes = max_routes
        finder.prepare_tree()
        finder.tree_search()
        finder.build_routes()
        routes = [route.to_dict() for route in finder.routes]
        return {
            "status": "ok",
            "target_smiles": finder.target_smiles,
            "num_routes": len(routes),
            "solved_routes": sum(
                1 for r in routes if _route_is_solved(r)
            ),
            "statistics": finder.extract_statistics(),
            "routes": routes,
        }
    except Exception as exc:  # noqa: BLE001 - surface engine errors
        return {"status": "error", "error": f"retrosynthesis run failed: {exc}"}


def _route_is_solved(node: dict) -> bool:
    """A route dict is solved when every leaf molecule is in stock."""
    children = node.get("children") or []
    if not children:
        return bool(node.get("in_stock", False))
    return all(_route_is_solved(child) for child in children)


def _walk_route(node: dict, depth: int, out: dict) -> None:
    out["max_depth"] = max(out["max_depth"], depth)
    node_type = node.get("type")
    children = node.get("children") or []
    if node_type == "reaction":
        out["num_reactions"] += 1
        meta = node.get("metadata") or {}
        if isinstance(meta, dict):
            for key in ("policy_name", "template", "classification"):
                if key in meta:
                    out.setdefault("reaction_metadata_examples", {}).setdefault(
                        key, str(meta[key])
                    )
    if node_type == "mol":
        out["num_molecules"] += 1
        if not children:
            out["building_blocks"].append(
                {
                    "smiles": node.get("smiles"),
                    "in_stock": bool(node.get("in_stock", False)),
                }
            )
    for child in children:
        _walk_route(child, depth + 1, out)


@mcp.tool()
def summarize_route(route_json: str) -> dict:
    """Summarise one AiZynthFinder route (JSON string from plan_retrosynthesis
    or from aizynthcli output) in plain terms.

    Returns: whether the route is solved (all building blocks in stock),
    number of reaction steps, tree depth, and the list of leaf building
    blocks with their in-stock status. Needs no model files.
    """
    try:
        route = json.loads(route_json)
    except (json.JSONDecodeError, TypeError) as exc:
        return {"status": "error", "error": f"route_json is not valid JSON: {exc}"}
    if not isinstance(route, dict) or "smiles" not in route:
        return {
            "status": "error",
            "error": "route_json is not an AiZynthFinder route dict "
            "(expected a dict with 'smiles', 'type' and 'children')",
        }
    out: dict = {
        "max_depth": 0,
        "num_reactions": 0,
        "num_molecules": 0,
        "building_blocks": [],
    }
    _walk_route(route, 0, out)
    out["target_smiles"] = route.get("smiles")
    out["solved"] = all(
        block["in_stock"] for block in out["building_blocks"]
    )
    out["num_building_blocks"] = len(out["building_blocks"])
    out["building_blocks_in_stock"] = sum(
        1 for block in out["building_blocks"] if block["in_stock"]
    )
    out["status"] = "ok"
    out["summary"] = (
        f"Route for {out['target_smiles']}: {out['num_reactions']} reaction "
        f"step(s), {out['num_building_blocks']} building block(s), "
        f"{out['building_blocks_in_stock']} in stock — "
        f"{'SOLVED' if out['solved'] else 'NOT solved (some building blocks are not in stock)'}."
    )
    return out


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="print the tool list and engine status, then exit",
    )
    args = parser.parse_args()
    if args.check:
        tools = sorted(mcp._tool_manager._tools.keys())
        print("tools:", json.dumps(tools, indent=2))
        print("status:", json.dumps(aizynth_status(), indent=2, default=str))
        return
    mcp.run()


if __name__ == "__main__":
    _main()
