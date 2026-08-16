ai_report_generator/
│
├── app.py
├── config.py
├── models.py
├── workflow.py
├── tools.py
├── prompts.py
├── report_generator.py
├── utils.py
│
├── agents/
│   ├── __init__.py
│   ├── input_analyzer.py
│   ├── planner.py
│   ├── researcher.py
│   ├── analyst.py
│   ├── validator.py
│   └── report_writer.py
│
├── requirements.txt
├── .env
└── .gitignore



                         USER
                           │
                           ▼
                    ┌─────────────┐
                    │    app.py   │
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
                    │ workflow.py │
                    │    MAF      │
                    └──────┬──────┘
                           │
                           ▼
                  Input Analyzer
                           │
                           ▼
                       Planner
                           │
                           ▼
                      Researcher
                           │
                           ▼
                       Analyst
                           │
                           ▼
                      Validator
                       │       │
                     FAIL      PASS
                       │       │
                       └──→ Analyst
                               │
                               ▼
                         Report Writer
                               │
                               ▼
                       Report Generator
                         │          │
                         ▼          ▼
                        PDF        DOCX

| File                       | Purpose                                                                |
| -------------------------- | ---------------------------------------------------------------------- |
| `app.py`                   | Starts the application and receives the user's report request.         |
| `config.py`                | Loads API keys, model settings, and other configuration.               |
| `models.py`                | Defines structured data models exchanged between agents.               |
| `workflow.py`              | Orchestrates all agents using Microsoft Agent Framework.               |
| `tools.py`                 | Contains external tools such as web search and document search.        |
| `prompts.py`               | Stores prompts/instructions used by the agents.                        |
| `report_generator.py`      | Converts the final report into Markdown, PDF, or DOCX.                 |
| `utils.py`                 | Contains common helper functions and logging utilities.                |
| `agents/input_analyzer.py` | Analyzes and understands the user's query.                             |
| `agents/planner.py`        | Creates the report structure and research plan.                        |
| `agents/researcher.py`     | Collects relevant information using available tools.                   |
| `agents/analyst.py`        | Analyzes the collected information and identifies key findings.        |
| `agents/validator.py`      | Checks the research/report for quality, completeness, and consistency. |
| `agents/report_writer.py`  | Generates the final professional report content.                       |
| `agents/__init__.py`       | Makes the agents folder a Python package.                              |
| `requirements.txt`         | Lists all required Python packages.                                    |
| `.env`                     | Stores secret keys and environment-specific configuration.             |
| `.gitignore`               | Specifies files/folders that Git should ignore.                        |

