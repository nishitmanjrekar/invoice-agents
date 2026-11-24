# AI Invoice Processing Agent System

An AI-powered accounts payable automation system for invoice extraction, validation, risk detection, and human approval.

The system combines Google Gemini, LangGraph, FastAPI, and Streamlit to automate invoice processing while maintaining validation rules, security checks, and human oversight.

## Features

- **AI Invoice Extraction:** Extract structured invoice information using Google Gemini.
- **Vendor Validation:** Verify invoice vendors against registered vendor records.
- **Purchase Order Validation:** Check invoice details against purchase orders.
- **Duplicate Detection:** Identify potentially duplicated invoices.
- **Amount Mismatch Detection:** Detect discrepancies between invoice and purchase order amounts.
- **Unknown Vendor Detection:** Flag invoices from unregistered vendors.
- **Prompt Injection Detection:** Identify suspicious instructions in invoice documents.
- **Risk-Based Routing:** Route invoices for automatic approval or human review based on validation results.
- **Human Approval Workflow:** Support review, approval, and rejection of invoices.
- **Audit Logging:** Maintain records of invoice processing and review activities.
- **Cloud-Ready Architecture:** Support SQLite for local development and PostgreSQL for cloud deployments.
- **Object Storage:** Support Supabase S3-compatible object storage for uploaded documents.

## Technology Stack

| Component | Technologies |
|---|---|
| Programming language | Python |
| AI extraction | Google Gemini API |
| Agent orchestration | LangGraph |
| Backend | FastAPI |
| Frontend | Streamlit |
| Database | SQLite, PostgreSQL |
| ORM / SQL | SQLAlchemy |
| Object storage | Supabase S3-compatible storage |
| API server | Uvicorn |
| Testing | Pytest |
| Code quality | Ruff |
| Tool integration | Model Context Protocol (MCP) |

## Architecture

The application follows a modular architecture:

1. **Frontend:** Streamlit provides the invoice processing interface, dashboard, management pages, approval center, review cases, and audit logs.
2. **Backend:** FastAPI exposes endpoints for invoice processing and application workflows.
3. **AI Processing:** Gemini extracts structured information from invoice documents.
4. **Agent Workflow:** LangGraph orchestrates invoice processing and validation steps.
5. **Validation:** Business rules check vendors, purchase orders, invoice amounts, duplicates, and security risks.
6. **Decision Routing:** Invoices are routed for automatic approval or human review.
7. **Persistence:** SQLite supports local development, while PostgreSQL supports cloud deployment.
8. **Storage:** Supabase S3-compatible storage supports invoice document storage.

## Project Structure

```text
invoice-agents/
├── frontend/
│   ├── app.py
│   ├── api_client.py
│   ├── config.py
│   ├── components/
│   └── pages/
├── src/
│   ├── agents/
│   ├── mcp_servers/
│   ├── migrations/
│   ├── postgres_migrations/
│   ├── api.py
│   ├── auth.py
│   ├── extract.py
│   ├── rules.py
│   ├── review.py
│   ├── submissions.py
│   ├── object_storage.py
│   └── ...
├── tests/
├── tools/
├── data/
│   ├── inv_001.txt
│   ├── inv_001.json
│   ├── ...
│   └── manifest.json
├── docs/
│   └── baselines/
├── .env.example
├── .gitignore
├── pytest.ini
├── requirements.txt
├── requirements-frontend.txt
├── requirements-dev.txt
└── README.md
```

## Getting Started

### Prerequisites

- Python 3.12 recommended
- Google Gemini API key
- Git

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/invoice-agents.git
cd invoice-agents
```

Replace `YOUR_USERNAME` with your GitHub username.

### 2. Create a virtual environment

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

Install backend and development dependencies:

```powershell
pip install -r requirements-dev.txt
```

Install frontend dependencies:

```powershell
pip install -r requirements-frontend.txt
```

### 4. Configure environment variables

Create a local environment file:

```powershell
Copy-Item .env.example .env
```

Open `.env` and configure the required values.

At minimum, provide your Gemini API key:

```dotenv
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-3.5-flash-lite
```

Additional configuration:

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Google Gemini API authentication |
| `GEMINI_MODEL` | Gemini model used for extraction |
| `INVOICE_AUTH` | Application authentication setting |
| `INVOICE_AUTH_SECRET` | Authentication signing secret |
| `DATABASE_URL` | PostgreSQL connection URL; empty uses SQLite |
| `INVOICE_API_URL` | FastAPI backend URL used by the frontend |
| `INVOICE_RECOVERY_ON_START` | Controls startup recovery |
| `SUBMISSION_RECOVERY_GRACE_SECONDS` | Recovery grace period |
| `OBJECT_STORAGE_ENDPOINT_URL` | S3-compatible storage endpoint |
| `OBJECT_STORAGE_REGION` | Storage region |
| `OBJECT_STORAGE_ACCESS_KEY_ID` | Storage access key |
| `OBJECT_STORAGE_SECRET_ACCESS_KEY` | Storage secret key |
| `OBJECT_STORAGE_BUCKET` | Storage bucket |

**Object storage configuration:** All five `OBJECT_STORAGE_*` variables are required when using Supabase S3-compatible storage. Configure the endpoint URL, region, access key ID, secret access key, and bucket name in your backend environment. Do not commit storage credentials.

See `.env.example` for the configuration template.

**Security:** Never commit `.env`, API keys, database credentials, authentication secrets, or storage credentials.
For production deployments, enable application authentication using `INVOICE_AUTH` and configure a strong, unique `INVOICE_AUTH_SECRET`. Never reuse development secrets or commit production credentials.

## Running the Application

Start the FastAPI backend from the project root:

```powershell
uvicorn src.api:app --reload
```

The backend runs locally at:

```text
http://127.0.0.1:8000
```

FastAPI API documentation is available at:

```text
http://127.0.0.1:8000/docs
```

Open a separate terminal, activate the virtual environment, and start the Streamlit frontend:

```powershell
streamlit run frontend/app.py
```

The frontend connects to the local backend by default. To use a different backend, configure `INVOICE_API_URL` in the frontend environment.

The application supports:

- **SQLite:** Default database for local development.
- **PostgreSQL:** Supported for cloud deployment through `DATABASE_URL`.

### SQLite migrations

```powershell
python -m src.migrate status
python -m src.migrate upgrade --create
python -m src.migrate verify

## Synthetic Test Dataset

The repository includes a reproducible synthetic invoice dataset containing 150 invoice examples.

| Category | Number of invoices |
|---|---:|
| Clean | 90 |
| Amount mismatch | 20 |
| Duplicate | 15 |
| Unknown vendor | 15 |
| Prompt injection | 10 |
| **Total** | **150** |

The dataset includes invoice text documents, corresponding JSON labels, and a manifest containing dataset statistics.

These examples are synthetic and are provided for development, testing, and evaluation.

## Testing

Run the complete test suite:

```powershell
python -m pytest -q
```

Run code quality checks:

```powershell
ruff check .
```
### PostgreSQL migrations

Before running these commands, configure `DATABASE_URL` with your PostgreSQL connection URL.

```powershell
python -m src.migrate_postgres status
python -m src.migrate_postgres upgrade
python -m src.migrate_postgres verify

> **Important:**
> - Keep `DATABASE_URL` empty for local SQLite development.
> - Set `DATABASE_URL` to your PostgreSQL connection URL before running PostgreSQL migrations.
> - SQLite and PostgreSQL use separate migration runners.
> - Never commit real database credentials or connection URLs.

## Offline Evaluation

The project includes an offline rules-engine evaluation baseline under `docs/baselines/`.

The recorded baseline reports:

- 150 synthetic invoices evaluated.
- 100% routing accuracy.
- 100% exact issue accuracy.
- 100% issue precision, recall, and F1.

**Important:** These figures represent evaluation against the labelled synthetic dataset using the offline rules engine. They do not establish real-world accuracy or measure Gemini extraction accuracy.

## Deployment

The project is designed to support a cloud deployment architecture using:

- Streamlit Community Cloud for the frontend.
- Render for the FastAPI backend.
- Neon PostgreSQL for database hosting.
- Supabase Storage for invoice documents.
- Google Gemini API for AI extraction.

Deployment requires configuring the appropriate environment variables and service credentials in the respective hosting platforms.

Free-tier availability, resource limits, and service policies may change.

## Security Considerations

- Keep API keys and credentials outside source control.
- Use strong authentication secrets in deployed environments.
- Keep uploaded invoice documents private.
- Do not expose internal database information through public endpoints.
- Review flagged invoices before approving them.
- Treat invoice content as untrusted input.

## License

Add a license before distributing or reusing this project publicly.
