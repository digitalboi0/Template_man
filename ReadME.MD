
## Setup Instructions (Local Development)

1.  **Clone the Repository:**
    ```bash
    git clone <your_template_service_repository_url>
    cd template_man
    ```

2.  **Create and Activate Virtual Environment:**
    ```bash
    python -m venv venv
    source venv/bin/activate # On Linux/macOS
    # On Windows: venv\Scripts\activate
    ```

3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Configure Environment Variables:**
    Create a `.env` file in the project root (`template_man/.env`).
    ```env
    # template_man/.env
    SECRET_KEY=your_strong_secret_key_for_template_service_here_456!@#
    INTERNAL_API_SECRET=7175326e-9606-4fae-abe3-bc2ac6e55ea0 # Must match the gateway's secret
    # Database (Example using SQLite for dev)
    DB_ENGINE=django.db.backends.sqlite3
    DB_NAME=db.sqlite3
    # DB_HOST=localhost # Not needed for SQLite
    # DB_PORT=5432       # Not needed for SQLite
    # DB_USER=postgres   # Not needed for SQLite
    # DB_PASSWORD=postgres # Not needed for SQLite
    # Redis
    REDIS_URL=redis://localhost:6379/2 # Use a different DB number for isolation if needed
    # Debug Mode
    DEBUG=True
    ALLOWED_HOSTS=localhost,127.0.0.1
    ```

5.  **Run Migrations:** This creates the necessary database tables for `Organization`, `Template`, and `TemplateUsageLog`.
    ```bash
    python manage.py migrate
    ```

6.  **Start the Template Service Server:**
    ```bash
    python manage.py runserver 8002 # Or python manage.py runserver 0.0.0.0:8002 if accessed from another machine
    ```

## API Endpoints (Template Service)

### 1. Health Check

*   **Endpoint:** `GET /health/`
*   **Description:** Provides a health status check for the template service and its dependencies (Database, Redis, Cache, Template Count).
*   **Authentication:** None required.
*   **Headers:** None required.
*   **Request Body:** None.
*   **Example Request:**
    ```bash
    curl -X GET http://127.0.0.1:8002/health/
    ```
*   **Response:**
    *   **Status:** `200 OK` if healthy, `503 Service Unavailable` if unhealthy.
    *   **Body:** JSON object containing status, timestamp, service name, version, and dependency checks.

### 2. Create Template

*   **Endpoint:** `POST /api/v1/templates/`
*   **Description:** Creates a new template associated with the requesting organization.
*   **Authentication:** Requires `X-Internal-Secret` header.
*   **Authorization:** Requires `X-Organization-ID` header to specify which organization the template belongs to.
*   **Headers:**
    *   `X-Internal-Secret: <your_internal_secret>` (Required)
    *   `X-Organization-ID: <organization_id>` (Required)
    *   `Content-Type: application/json` (Required for JSON body)
*   **Request Body (JSON):**
    ```json
    {
      "code": "string",              // (Required) Unique template code within the organization
      "name": "string",              // (Required) Template name
      "description": "string",       // (Optional) Description
      "type": "email|push|sms",      // (Required) Template type
      "subject": "string",           // (Conditional) Required for 'email' type
      "content": "string",           // (Required) Template content (plaintext)
      "html_content": "string",      // (Optional) HTML version for 'email' type
      "language": "en",              // (Optional) Language code (default: en)
      "status": "draft|active",      // (Optional) Initial status (default: draft)
      "variables": ["string"],       // (Optional) Required variables for rendering
      "optional_variables": ["string"] // (Optional) Optional variables for rendering
    }
    ```
*   **Example Request:**
    ```bash
    curl -X POST http://127.0.0.1:8002/api/v1/templates/ \
         -H "Content-Type: application/json" \
         -H "X-Internal-Secret: 7175326e-9606-4fae-abe3-bc2ac6e55ea0" \
         -H "X-Organization-ID: 99c97b86-03bd-4fb6-90b8-617b6a6cae10" \
         -d '{
               "code": "welcome_email_org_scoped",
               "name": "Welcome Email (Org Scoped)",
               "description": "Welcome template for users in TestOrg",
               "type": "email",
               "subject": "Welcome, {{ name }}!",
               "content": "Hi {{ name }},\n\nThanks for joining TestOrg!\n\nBest regards,\nThe Team",
               "html_content": "<html><body><p>Hi {{ name }},</p><p>Thanks for joining <strong>TestOrg</strong>!</p><p>Best regards,<br>The Team</p></body></html>",
               "language": "en",
               "status": "active",
               "variables": ["name"],
               "optional_variables": []
             }'
    ```
*   **Response (Success):**
    *   **Status:** `201 Created`
    *   **Body:** JSON object containing the created template data.
*   **Response (Error):**
    *   **Status:** `400 Bad Request`, `401 Unauthorized`, `403 Forbidden`, `409 Conflict`, `500 Internal Server Error`
    *   **Body:** JSON object describing the error.

### 3. Get Template by Code

*   **Endpoint:** `GET /api/v1/templates/<code>/`
*   **Description:** Retrieves the *active*, *default* template for a specific code and organization.
*   **Authentication:** Requires `X-Internal-Secret` header.
*   **Authorization:** Requires `X-Organization-ID` header. The template must belong to the specified organization.
*   **Headers:**
    *   `X-Internal-Secret: <your_internal_secret>` (Required)
    *   `X-Organization-ID: <organization_id>` (Required)
*   **Request Body:** None.
*   **Example Request:**
    ```bash
    curl -X GET http://127.0.0.1:8002/api/v1/templates/welcome_email_org_scoped/ \
         -H "X-Internal-Secret: 7175326e-9606-4fae-abe3-bc2ac6e55ea0" \
         -H "X-Organization-ID: 99c97b86-03bd-4fb6-90b8-617b6a6cae10"
    ```
*   **Response (Success):**
    *   **Status:** `200 OK`
    *   **Body:** JSON object containing the template data.
*   **Response (Error):**
    *   **Status:** `400 Bad Request`, `401 Unauthorized`, `403 Forbidden`, `404 Not Found`, `500 Internal Server Error`
    *   **Body:** JSON object describing the error.

### 4. Update Template (Creates New Version)

*   **Endpoint:** `PUT /api/v1/templates/<code>/`
*   **Description:** Creates a new version of an existing template within the organization.
*   **Authentication:** Requires `X-Internal-Secret` header.
*   **Authorization:** Requires `X-Organization-ID` header. The template must belong to the specified organization.
*   **Headers:**
    *   `X-Internal-Secret: <your_internal_secret>` (Required)
    *   `X-Organization-ID: <organization_id>` (Required)
    *   `Content-Type: application/json` (Required for JSON body)
*   **Request Body (JSON):** Include fields to update (e.g., `name`, `content`, `subject`). Omitting a field keeps its current value.
*   **Example Request:**
    ```bash
    curl -X PUT http://127.0.0.1:8002/api/v1/templates/welcome_email_org_scoped/ \
         -H "Content-Type: application/json" \
         -H "X-Internal-Secret: 7175326e-9606-4fae-abe3-bc2ac6e55ea0" \
         -H "X-Organization-ID: 99c97b86-03bd-4fb6-90b8-617b6a6cae10" \
         -d '{
               "name": "Updated Welcome Email (Org Scoped)",
               "content": "Hi {{ name }},\n\nThank you for joining TestOrg! We are excited to have you.\n\nBest regards,\nThe Team"
             }'
    ```
*   **Response (Success):**
    *   **Status:** `200 OK`
    *   **Body:** JSON object containing the *new* version of the template data.
*   **Response (Error):**
    *   **Status:** `400 Bad Request`, `401 Unauthorized`, `403 Forbidden`, `404 Not Found`, `500 Internal Server Error`
    *   **Body:** JSON object describing the error.

### 5. Manage Template Lifecycle (Publish/Archive)

*   **Endpoint:** `PATCH /api/v1/templates/<code>/`
*   **Description:** Publishes or archives the latest version of a template within the organization.
*   **Authentication:** Requires `X-Internal-Secret` header.
*   **Authorization:** Requires `X-Organization-ID` header. The template must belong to the specified organization.
*   **Headers:**
    *   `X-Internal-Secret: <your_internal_secret>` (Required)
    *   `X-Organization-ID: <organization_id>` (Required)
    *   `Content-Type: application/json` (Required for JSON body)
*   **Request Body (JSON):**
    ```json
    {
      "action": "publish|archive", // (Required) The action to perform
      "language": "en"             // (Optional) Language code (default: en)
    }
    ```
*   **Example Request (Publish):**
    ```bash
    curl -X PATCH http://127.0.0.1:8002/api/v1/templates/welcome_email_org_scoped/ \
         -H "Content-Type: application/json" \
         -H "X-Internal-Secret: 7175326e-9606-4fae-abe3-bc2ac6e55ea0" \
         -H "X-Organization-ID: 99c97b86-03bd-4fb6-90b8-617b6a6cae10" \
         -d '{
               "action": "publish",
               "language": "en"
             }'
    ```
*   **Response (Success):**
    *   **Status:** `200 OK`
    *   **Body:** JSON object containing the updated template data (status changed).
*   **Response (Error):**
    *   **Status:** `400 Bad Request`, `401 Unauthorized`, `403 Forbidden`, `404 Not Found`, `500 Internal Server Error`
    *   **Body:** JSON object describing the error.

### 6. Internal Template Render Endpoint

*   **Endpoint:** `POST /internal/templates/render/`
*   **Description:** **Internal endpoint for the Notification Gateway only.** Renders a template with provided variables.
*   **Authentication:** Requires `X-Internal-Secret` header.
*   **Authorization:** Requires `X-Organization-ID` header (passed by the gateway).
*   **Headers:**
    *   `X-Internal-Secret: <your_internal_secret>` (Required)
    *   `X-Organization-ID: <organization_id>` (Required - passed by gateway)
    *   `Content-Type: application/json` (Required for JSON body)
*   **Request Body (JSON):**
    ```json
    {
      "template_code": "string",       // (Required) Code of the template to render
      "language": "string",            // (Optional) Language code (default: en)
      "variables": {                   // (Required) Variables to fill the template placeholders
        "name": "John Doe",
        "company": "Acme Inc"
      },
      "notification_id": "string",     // (Required) ID of the notification being processed
      "organization_id": "string"      // (Required) ID of the organization making the request (passed by gateway)
    }
    ```
*   **Example Request (from Gateway):**
    *(This request is typically made by the Notification Gateway, not manually)*
*   **Response (Success):**
    *   **Status:** `200 OK`
    *   **Body:** JSON object containing the rendered subject, content, HTML content, template ID, version, and render time.
*   **Response (Error):**
    *   **Status:** `400 Bad Request`, `401 Unauthorized`, `403 Forbidden`, `404 Not Found`, `422 Unprocessable Entity`, `500 Internal Server Error`
    *   **Body:** JSON object describing the error.

### 7. API Documentation (Swagger UI)

*   **Swagger UI Endpoint:** `GET /api/docs/`
*   **Description:** Interactive API documentation for the template service's public and internal API endpoints, generated using `drf-spectacular`.
*   **Authentication:** None required for viewing the documentation UI itself. Individual endpoints within the UI will require appropriate authentication (e.g., `X-Internal-Secret` for internal endpoints, potentially the gateway's `X-API-Key` if public endpoints are added later).
*   **Headers:** None required for accessing the UI page itself.
*   **Request Body:** None.
*   **Example Request:**
    ```bash
    curl -X GET http://127.0.0.1:8002/api/docs/
    ```
    *(Or simply open `http://127.0.0.1:8002/api/docs/` in your web browser).*
*   **Response:**
    *   **Status:** `200 OK`
    *   **Body:** HTML page rendering the Swagger UI interface.

*   **OpenAPI Schema Endpoint:** `GET /api/schema/`
*   **Description:** Serves the OpenAPI 3.0 schema definition file for the template service's API. This file is consumed by Swagger UI and ReDoc.
*   **Authentication:** None required.
*   **Headers:** None required.
*   **Request Body:** None.
*   **Example Request:**
    ```bash
    curl -X GET http://127.0.0.1:8002/api/schema/
    ```
*   **Response:**
    *   **Status:** `200 OK`
    *   **Body:** JSON object containing the OpenAPI schema.

## Management Commands

### Sync Organization (Internal)

*(Handled by the gateway's `create_org` command, which calls the internal endpoint below)*

*   **Endpoint Called by Gateway:** `POST /mock/organizations/` (or the new internal sync endpoint if added to the gateway's view)
*   **Description:** Receives organization data from the Notification Gateway and stores it in the template service's database for scoping purposes. This endpoint requires the `X-Internal-Secret` header.
*   **Headers:**
    *   `X-Internal-Secret: <your_internal_secret>` (Required)
    *   `Content-Type: application/json` (Required for JSON body)
*   **Request Body (JSON):** Contains organization details (ID, name, plan, quota, etc.).

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

