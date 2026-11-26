## 🎬 Admin Demo Video

Watch the complete walkthrough of the AI Invoice Processing Agent System, demonstrating the admin interface and end-to-end invoice processing workflow.

[▶ Watch the Admin Demo Video](https://drive.google.com/file/d/1xNYOqdNzZYZi15yvatubXnt5weukkOCB/view?usp=sharing)

The walkthrough covers:
- Admin login and dashboard overview
- Invoice processing and AI extraction
- Invoice management and validation results
- Human approval and rejection workflows
- Review case management
- Audit logs and activity tracking

**Live Application:** https://invoice-agents.streamlit.app/

## ✨ Features

### AI-Powered Invoice Processing
- **AI Invoice Extraction:** Extract structured invoice information using Google Gemini.
- **Vendor Validation:** Verify vendors against registered vendor records.
- **Purchase Order Validation:** Verify purchase orders and vendor associations.
- **Amount Verification:** Detect discrepancies between invoice and purchase order amounts.
- **Duplicate Detection:** Identify previously submitted invoices.
- **Unknown Vendor Detection:** Flag unregistered or unapproved vendors.
- **Prompt Injection Detection:** Identify suspicious instructions embedded in invoice documents.
- **Risk-Based Routing:** Route invoices for automatic processing or human review.

### Workflow Automation
- LangGraph-powered invoice processing workflows.
- Human approval and rejection workflows.
- Review-case creation for high-risk invoices.
- Persistent workflow checkpoints.
- Submission tracking and startup recovery.
- Workflow status monitoring.
- Audit logging of important actions.

### Application Dashboard
- Operational invoice statistics.
- Invoice status breakdowns.
- Invoice processing interface.
- Invoice management and inspection.
- Human approval center.
- Review case management.
- Audit log viewer.

### Security and Access Control
- JWT-based authentication.
- Role-based access control (RBAC).
- Four supported roles: Admin, Finance Manager, Reviewer, and Uploader.
- Uploader-specific invoice and workflow visibility.
- Staff-only approval, review-case, and audit-log access.
- Prevention of users approving their own invoice submissions.
- Private invoice document storage.

## 👥 User Roles and Permissions

| Feature | Admin | Finance Manager | Reviewer | Uploader |
|---|:---:|:---:|:---:|:---:|
| Dashboard | ✓ | ✓ | ✓ | ✓ |
| Upload invoices | ✓ | ✓ | ✓ | ✓ |
| Invoice Management | ✓ | ✓ | ✓ | ✓ |
| View all invoices | ✓ | ✓ | ✓ | — |
| View own invoices | ✓ | ✓ | ✓ | ✓ |
| Approval Center | ✓ | ✓ | ✓ | — |
| Review Cases | ✓ | ✓ | ✓ | — |
| Audit Logs | ✓ | ✓ | ✓ | — |

**Security rules:**
- Uploaders can access only their own invoices and workflows.
- Staff members can access organization-wide invoice records.
- Only authorized staff can perform approval and review actions.
- Users cannot approve their own invoice submissions.
- Unauthorized access to uploader-restricted resources is denied.

## 🏗️ Architecture

```text
                 Streamlit Frontend
                         |
                         | HTTP / REST API
                         |
                    FastAPI Backend
                         |
                  LangGraph Workflow
                         |
          +--------------+--------------+
          |              |              |
     Gemini AI      Validation      Risk Detection
     Extraction     Engine          Engine
          |              |              |
          +--------------+--------------+
                         |
                 Workflow Routing
                         |
             +-----------+-----------+
             |                       |
        Auto Processing         Human Review
                                     |
                              Approval / Rejection
                                     |
                              Audit Logging
                         |
              PostgreSQL Database
                         |
                  Supabase Storage
```

## 🛠️ Technology Stack

| Component | Technologies |
|---|---|
| Programming Language | Python |
| AI Extraction | Google Gemini API |
| Agent Orchestration | LangGraph |
| Backend | FastAPI |
| Frontend | Streamlit |
| Database | SQLite, PostgreSQL |
| ORM / SQL | SQLAlchemy |
| Authentication | JWT, Role-Based Access Control |
| Object Storage | Supabase S3-Compatible Storage |
| Deployment | Render, Streamlit Community Cloud, Neon |
| Testing | Pytest |
| Code Quality | Ruff |

## 🧪 Testing

The project includes automated tests covering API functionality, authentication, authorization, invoice processing, workflow behavior, and database operations.

**Latest verified test results:**
- 452 automated tests passed.
- Ruff code quality checks passed.

Run the complete test suite:

```powershell
python -m pytest -q
```

Run code quality checks:

```powershell
python -m ruff check .
```

## 📊 Synthetic Invoice Dataset

The repository includes 150 synthetic invoice examples:

| Category | Number |
|---|---:|
| Clean | 90 |
| Amount Mismatch | 20 |
| Duplicate | 15 |
| Unknown Vendor | 15 |
| Prompt Injection | 10 |
| **Total** | **150** |

The dataset supports development, testing, and evaluation.

## ☁️ Deployment

The production application uses a cloud-based architecture:

- **Frontend:** Streamlit Community Cloud
- **Backend:** Render
- **Database:** Neon PostgreSQL
- **Document Storage:** Supabase Storage
- **AI Provider:** Google Gemini API

Local development supports SQLite, while PostgreSQL is used for cloud deployment.

## 🔐 Security Considerations

- API keys and credentials are stored outside source control.
- Production authentication is enforced.
- Invoice documents are stored privately.
- Role-based permissions restrict access to application resources.
- Invoice contents are treated as untrusted input.
- Human oversight is required for invoices routed to review.

## 📌 Project Status

**Deployed and operational.**

The application supports end-to-end invoice submission, AI extraction, validation, risk-based routing, human decisions, and audit logging.

---

## 👨‍💻 Author

**Nishit Manjrekar**

- GitHub: https://github.com/nishitmanjrekar
- LinkedIn: https://www.linkedin.com/in/nishitmanjrekar/

