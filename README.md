# CostGuard

Azure Infrastructure Cost Impact Analyzer for Terraform Plans.

CostGuard parses Terraform plan JSON, queries the live Azure Retail Prices API, and provides a strict budget enforcement mechanism for CI/CD pipelines.

## Installation

Requires Python 3.11+.

```bash
pip install -e .
```

*Note for Windows users:* Ensure your Python `Scripts` directory (e.g. `C:\Users\user\AppData\Local\Python\pythoncore-3.14-64\Scripts`) is in your system `PATH`. Alternatively, you can always run the tool via `python -m costguard`.

## Usage

CostGuard accepts Terraform plan JSON either via `stdin` or the `--plan` file flag.

```bash
# Using stdin (typical CI/CD pipeline usage)
terraform plan -out tfplan.binary
terraform show -json tfplan.binary | costguard --max-increase 50.00

# Using a file
costguard --plan test-plans/plan_a_small_add.json --max-increase 25.00
```

### Options

- `--plan FILE`: Path to the plan JSON.
- `--max-increase N`: Budget threshold. If net cost exceeds this amount, exits with code 1.
- `--currency CODE`: USD, EUR, GBP, or INR (default: USD).
- `--markdown`: Outputs a GitHub PR-ready Markdown table.
- `--json`: Outputs machine-readable JSON.
- `--clear-cache`: Purges the local SQLite cache and exits.

## Testing

Run the included pytest suite:
```bash
pip install -e .[dev]
pytest -v
```

Execute manual tests against included mock plans:
```bash
costguard --plan test-plans/plan_a_small_add.json --max-increase 50
costguard --plan test-plans/plan_b_upgrade_delete.json
costguard --plan test-plans/plan_c_hostile_noise.json
costguard --plan test-plans/plan_d_metadata_only.json
costguard --plan test-plans/plan_e_corrupt.json
```
