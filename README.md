AI Invoice Processing Agent System

An AI-powered invoice processing and accounts payable automation system that extracts invoice information, validates business rules, detects potential fraud and anomalies, and manages human approval workflows.

Built with Python, Google Gemini, LangGraph, FastAPI, Streamlit, and PostgreSQL, the application combines AI-driven automation with role-based access control, persistent workflows, and auditability.

Live Application

Application: https://invoice-agents.streamlit.app/

The application is deployed using Streamlit Community Cloud, Render, Neon PostgreSQL, and Supabase Storage.

Public Demo Credentials

Visitors and recruiters can use the following dedicated uploader account to explore the application.

Field| Credentials
Username| "demo_uploader"
Password| "uploader@2405"
Role| Uploader

What you can do with the demo account:

- Access the application dashboard.
- Upload synthetic invoice documents.
- Explore AI-powered invoice extraction and validation.
- Track invoice processing workflows.
- View submitted invoices and their statuses.
- Inspect extracted invoice information and validation results.

Demo account restrictions:

- Can only view invoices and workflows submitted by this account.
- Cannot access administrative pages.
- Cannot approve or reject invoices.
- Cannot access organization-wide audit logs or review cases.

Important: This is a shared public demo account. Uploaded documents and resulting records are stored in the shared production environment. Please upload synthetic sample invoices only. Never upload confidential, personal, or real financial documents.

Administrative credentials are private and are not published.

Admin Demo Video

Watch the recorded walkthrough demonstrating the administrative interface and invoice processing workflows.

"Watch the Admin Demo Video" (https://drive.google.com/file/d/1xNYOqdNzZYZi15yvatubXnt5weukkOCB/view?usp=sharing)

The walkthrough covers:

- Admin dashboard and invoice statistics.
- Invoice processing and AI extraction.
- Invoice management and validation results.
- Human approval and rejection workflows.
- Review case management.
- Audit logs and activity tracking.

Key Features

1. AI-Powered Invoice Extraction

- Extract structured invoice information using Google Gemini.
- Identify vendor details, invoice numbers, dates, amounts, currencies, purchase orders, and line items.
- Process uploaded plain-text invoice documents.
- Convert unstructured invoice information into structured data.

2. Intelligent Invoice Validation

The system performs multiple validation checks:

Validation| Purpose
Vendor Validation| Verify that the vendor is registered and approved.
Purchase Order Validation| Verify PO existence and vendor association.
PO Amount Validation| Detect invoice and purchase order amount mismatches.
Line Item Validation| Verify line-item calculations against invoice totals.
Duplicate Detection| Identify previously recorded duplicate invoices.
Security Validation| Detect suspicious prompt-injection instructions.

3. Risk-Based Workflow Routing

Invoices are automatically evaluated and routed based on validation results.

- Auto-approval routing for invoices passing applicable checks.
- Human approval routing when a decision is required.
- Human review cases for high-risk or exceptional invoices.
- Detection of unknown vendors and suspicious invoice content.
- Persistent workflow state and status tracking.

4. Workflow Automation

- LangGraph-based invoice processing.
- Persistent workflow checkpoints.
- Human-in-the-loop approval and rejection.
- Workflow resumption after human decisions.
- Submission tracking and idempotency.
- Failure recording and startup recovery.
- Audit history for important workflow actions.

5. Dashboard and Invoice Management

- Invoice processing statistics.
- Pending approval, human review, approved, and rejected counts.
- Invoice search and filtering.
- Invoice details and extracted information.
- Validation results and risk indicators.
- Workflow status and submission tracking.

6. Human Approval Center

Authorized staff can:

- View invoices awaiting a human decision.
- Inspect validation information.
- Approve eligible invoices.
- Reject invoices with a reason.
- Resume saved workflows after decisions.

Approval decisions are recorded and reflected in invoice and workflow status.

Note: The application records approval decisions; it does not initiate payments.

7. Review Case Management

- Centralized review of exceptional invoices.
- Identification of high-risk invoices.
- Review case status tracking.
- Human rejection of review cases.
- Auditability of review decisions.

8. Audit Logging

- Records important application actions.
- Tracks human approval and rejection decisions.
- Maintains an activity history.
- Provides visibility into workflow events and administrative operations.

Role-Based Access Control

The application implements four user roles.

Feature| Admin| Finance Manager| Reviewer| Uploader
Dashboard| Yes| Yes| Yes| Yes
Upload Invoices| Yes| Yes| Yes| Yes
Invoice Management| Yes| Yes| Yes| Yes
View All Invoices| Yes| Yes| Yes| No
View Own Invoices| Yes| Yes| Yes| Yes
Approval Center| Yes| Yes| Yes| No
Review Cases| Yes| Yes| Yes| No
Audit Logs| Yes| Yes| Yes| No

Security Controls

- JWT-based authentication.
- Database-backed role verification.
- Role-based API authorization.
- Uploader-specific invoice and workflow visibility.
- Staff-only access to administrative resources.
- Prevention of unauthorized workflow decisions.
- Prevention of self-approval: users cannot approve their own invoice submissions.
- Unauthorized uploader access to restricted resources is denied.

System Architecture

                 Streamlit Frontend
                         |
                         |
                    FastAPI Backend
                         |
                  Authentication
                         |
                  LangGraph Workflow
                         |
          +--------------+--------------+
          |              |              |
       Gemini AI     Validation     Risk Detection
       Extraction     Engine          Engine
          |              |              |
          +--------------+--------------+
                         |
                   Workflow Routing
                         |
              +----------+----------+
              |                     |
        Auto Processing        Human Decision
                                    |
                           Approval / Rejection
                                    |
                              Audit Logging
                         |
                 PostgreSQL Database
                         |
                  Supabase Storage

Technology Stack

Category| Technologies
Programming Language| Python
AI Model| Google Gemini
Agent Orchestration| LangGraph
Backend Framework| FastAPI
Frontend Framework| Streamlit
Database| PostgreSQL, SQLite
Database Toolkit| SQLAlchemy
Authentication| JWT
Access Control| Role-Based Access Control
Document Storage| Supabase S3-Compatible Storage
Deployment| Render, Streamlit Community Cloud
Database Hosting| Neon
Testing| Pytest
Code Quality| Ruff

Synthetic Invoice Dataset

The project includes a dataset of 150 synthetic invoices designed to test different processing and validation scenarios.

Invoice Category| Count
Clean Invoices| 90
Amount Mismatch| 20
Duplicate Invoices| 15
Unknown Vendors| 15
Prompt Injection| 10
Total| 150

The dataset is intended for development, testing, and workflow evaluation.

Testing and Code Quality

The project includes automated tests covering invoice processing, API endpoints, authentication, authorization, database operations, and workflow functionality.

Latest verified results:

- 452 automated tests passed
- Ruff checks passed

Run the test suite:

python -m pytest -q

Run code quality checks:

python -m ruff check .

Local Development

Prerequisites

- Python 3.12 or compatible version.
- Git.
- Google Gemini API key.
- PostgreSQL for PostgreSQL-backed development, or SQLite for local development.

Clone the Repository

git clone https://github.com/nishitmanjrekar/invoice-agents.git
cd invoice-agents

Create a Virtual Environment

python -m venv .venv
.venv\Scripts\Activate.ps1

Install Dependencies

pip install -r requirements.txt

Configure Environment Variables

Configure the required environment variables locally:

GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=your_configured_gemini_model
DATABASE_URL=your_database_connection_string
INVOICE_AUTH=enforce
INVOICE_AUTH_SECRET=your_authentication_secret

Configure the required storage credentials when using Supabase document storage.

Never commit API keys, database credentials, or authentication secrets to source control.

Start the Backend

uvicorn src.api:app --reload

Start the Frontend

In another terminal:

streamlit run frontend/app.py

Deployment Architecture

The production application uses a cloud-based architecture:

Component| Hosting
Frontend| Streamlit Community Cloud
Backend API| Render
Production Database| Neon PostgreSQL
Document Storage| Supabase Storage
AI Processing| Google Gemini API

Live Application: https://invoice-agents.streamlit.app/

Project Highlights

- End-to-end AI-powered invoice processing.
- Structured information extraction using Google Gemini.
- Multi-stage invoice validation.
- Risk-based workflow routing.
- LangGraph orchestration and persistent checkpoints.
- Human-in-the-loop approval and review.
- JWT authentication and role-based access control.
- Self-approval prevention.
- Submission tracking and audit logging.
- Cloud deployment with PostgreSQL and private document storage.
- Automated test suite with 452 passing tests.

Future Enhancements

Potential areas for further development include:

- Advanced fraud detection and anomaly scoring.
- GST and tax compliance validation.
- Three-way matching between invoices, purchase orders, and goods receipts.
- Enhanced invoice analytics and reporting.
- Improved observability and workflow monitoring.
- More comprehensive financial reconciliation.

Author

Nishit Manjrekar

Electronics and Computer Science Engineering

- GitHub: https://github.com/nishitmanjrekar
- LinkedIn: https://www.linkedin.com/in/nishitmanjrekar/
