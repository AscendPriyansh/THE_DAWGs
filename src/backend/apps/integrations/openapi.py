def get_openapi_schema() -> dict:
    """
    Returns the canonical OpenAPI 3.1 schema for the Dogfood 2026 hackathon portal API.
    """
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Dogfood 2026 Hackathon Portal REST API",
            "version": "1.0.0",
            "description": "Comprehensive REST API for hackathon events, versioned submissions, private judging, results calculation, community comments, moderation, and scoped integrations.",
        },
        "servers": [{"url": "/", "description": "Local development server"}],
        "paths": {
            "/api/v1/auth/login/": {
                "post": {
                    "summary": "Authenticate user session",
                    "tags": ["Authentication"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "email": {"type": "string", "format": "email"},
                                        "password": {"type": "string"},
                                    },
                                    "required": ["email", "password"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Session authenticated successfully"},
                        "401": {"description": "Invalid credentials"},
                    },
                }
            },
            "/api/v1/auth/logout/": {
                "post": {
                    "summary": "End current user session",
                    "tags": ["Authentication"],
                    "responses": {"200": {"description": "Logged out successfully"}},
                }
            },
            "/api/v1/auth/me/": {
                "get": {
                    "summary": "Current authenticated user profile",
                    "tags": ["Authentication"],
                    "responses": {
                        "200": {"description": "User profile details"},
                        "401": {"description": "Not authenticated"},
                    },
                }
            },
            "/api/v1/events/": {
                "get": {
                    "summary": "List public events",
                    "tags": ["Events"],
                    "responses": {"200": {"description": "Array of published events"}},
                }
            },
            "/api/v1/events/{slug}/": {
                "get": {
                    "summary": "Event details",
                    "tags": ["Events"],
                    "parameters": [{"name": "slug", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Event metadata, phases, tracks, and prizes"}},
                }
            },
            "/api/v1/events/{slug}/me/next-action/": {
                "get": {
                    "summary": "Derived next action for requesting user in this event",
                    "tags": ["Events"],
                    "parameters": [{"name": "slug", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Next action summary with deadline and server time"}},
                }
            },
            "/api/v1/events/{slug}/projects/": {
                "get": {
                    "summary": "Public project gallery for event",
                    "tags": ["Submissions"],
                    "parameters": [
                        {"name": "slug", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "track", "in": "query", "required": False, "schema": {"type": "string"}},
                        {"name": "q", "in": "query", "required": False, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "List of submitted projects"}},
                }
            },
            "/api/v1/projects/{id}/": {
                "get": {
                    "summary": "Public project details",
                    "tags": ["Submissions"],
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}],
                    "responses": {"200": {"description": "Project title, roster, and description"}},
                }
            },
            "/api/v1/workspace/save-draft/": {
                "post": {
                    "summary": "Save project draft revision with optimistic concurrency",
                    "tags": ["Workspace"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "event_slug": {"type": "string"},
                                        "title": {"type": "string"},
                                        "summary": {"type": "string"},
                                        "description_md": {"type": "string"},
                                        "track_id": {"type": "string", "format": "uuid"},
                                        "repo_url": {"type": "string"},
                                        "demo_url": {"type": "string"},
                                        "version": {"type": "integer"},
                                    },
                                    "required": ["event_slug", "title", "version"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Draft saved"},
                        "409": {"description": "StaleSaveConflict: version mismatch"},
                    },
                }
            },
            "/api/v1/workspace/submit/": {
                "post": {
                    "summary": "Formally submit project revision before deadline (Captain only)",
                    "tags": ["Workspace"],
                    "responses": {
                        "200": {"description": "Submission receipt with frozen roster snapshot"},
                        "403": {"description": "Submissions closed or user is not captain"},
                    },
                }
            },
            "/api/v1/events/{slug}/judging/assignments/": {
                "get": {
                    "summary": "Judge's own assigned review queue (Strict confidentiality)",
                    "tags": ["Judging"],
                    "parameters": [{"name": "slug", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "List of assignments for requesting judge"}},
                }
            },
            "/api/v1/assignments/{id}/review/submit/": {
                "post": {
                    "summary": "Submit evaluation review scores and private comment",
                    "tags": ["Judging"],
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}}],
                    "responses": {"200": {"description": "Review submitted successfully"}},
                }
            },
            "/api/v1/events/{slug}/results/preview/": {
                "post": {
                    "summary": "Private organiser scoring calculation preview",
                    "tags": ["Results"],
                    "parameters": [{"name": "slug", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {"description": "Calculated ResultRun preview with diagnostics"},
                        "403": {"description": "Organiser access required"},
                    },
                }
            },
            "/api/v1/events/{slug}/results/publish/": {
                "post": {
                    "summary": "Formally publish official leaderboard",
                    "tags": ["Results"],
                    "parameters": [{"name": "slug", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {"description": "Publication created and event judging/voting frozen"},
                        "400": {"description": "Judging or voting window still open"},
                    },
                }
            },
            "/api/v1/events/{slug}/results/": {
                "get": {
                    "summary": "Public official leaderboard (Serves active publication only)",
                    "tags": ["Results"],
                    "parameters": [{"name": "slug", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {"description": "Official published rankings and community results"},
                        "404": {"description": "Results have not been officially published yet"},
                    },
                }
            },
            "/api/v1/events/{slug}/projects/{project_id}/comments/": {
                "get": {
                    "summary": "List project comments",
                    "tags": ["Community"],
                    "parameters": [
                        {"name": "slug", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}},
                    ],
                    "responses": {"200": {"description": "List of visible comments"}},
                },
                "post": {
                    "summary": "Post a project comment",
                    "tags": ["Community"],
                    "parameters": [
                        {"name": "slug", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "project_id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}},
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"body": {"type": "string", "maxLength": 5000}},
                                    "required": ["body"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {"description": "Comment created"},
                        "429": {"description": "Rate limit exceeded (5/min or 50/day)"},
                    },
                },
            },
            "/api/v1/events/{slug}/moderation/inbox/": {
                "get": {
                    "summary": "Organiser moderation inbox",
                    "tags": ["Community"],
                    "parameters": [{"name": "slug", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {"description": "Open moderation cases and abuse signals"},
                        "403": {"description": "Organiser access required"},
                    },
                }
            },
            "/api/v1/events/{slug}/api-keys/": {
                "get": {
                    "summary": "List requesting user's scoped API keys for event",
                    "tags": ["Integrations"],
                    "parameters": [{"name": "slug", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Array of API credentials without raw secrets"}},
                },
                "post": {
                    "summary": "Generate a new high-entropy event-scoped API key",
                    "tags": ["Integrations"],
                    "parameters": [{"name": "slug", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string"},
                                        "scopes": {"type": "array", "items": {"type": "string"}},
                                        "expires_in_days": {"type": "integer", "default": 30},
                                    },
                                    "required": ["label", "scopes"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {"description": "Key issued. Raw bearer token returned once only."},
                        "400": {"description": "Invalid scopes or missing label"},
                    },
                },
            },
            "/api/v1/events/{slug}/api-keys/{id}/": {
                "delete": {
                    "summary": "Revoke an API key immediately",
                    "tags": ["Integrations"],
                    "parameters": [
                        {"name": "slug", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string", "format": "uuid"}},
                    ],
                    "responses": {"200": {"description": "API key revoked"}},
                }
            },
        },
        "components": {
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Scoped event API key in 'Authorization: Bearer dg_live_...'",
                },
                "CookieAuth": {
                    "type": "apiKey",
                    "in": "cookie",
                    "name": "session",
                    "description": "Django session cookie with CSRF protection",
                },
            }
        },
    }


def render_local_docs_html() -> str:
    """
    Renders clean, self-contained interactive API documentation with zero external CDN dependencies.
    """
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Dogfood 2026 — Local API Documentation</title>
  <style>
    :root {
      --bg: #09090b;
      --card-bg: #18181b;
      --border: #27272a;
      --text: #fafafa;
      --muted: #a1a1aa;
      --accent: #3b82f6;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --code-bg: #121215;
    }
    body {
      margin: 0;
      padding: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      line-height: 1.6;
    }
    header {
      background: #000;
      border-bottom: 1px solid var(--border);
      padding: 1.5rem 2rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    h1 { margin: 0; font-size: 1.4rem; font-weight: 700; letter-spacing: -0.02em; }
    .badge {
      background: #27272a;
      color: #fafafa;
      padding: 0.25rem 0.6rem;
      border-radius: 9999px;
      font-size: 0.75rem;
      font-weight: 600;
    }
    main { max-width: 1100px; margin: 2rem auto; padding: 0 1.5rem; }
    .intro {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1.5rem;
      margin-bottom: 2rem;
    }
    .endpoint {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-bottom: 1.25rem;
      overflow: hidden;
    }
    .endpoint-header {
      padding: 1rem 1.25rem;
      display: flex;
      align-items: center;
      gap: 1rem;
      cursor: pointer;
      user-select: none;
    }
    .endpoint-header:hover { background: #202024; }
    .method {
      font-weight: 700;
      font-size: 0.8rem;
      padding: 0.25rem 0.5rem;
      border-radius: 4px;
      min-width: 55px;
      text-align: center;
    }
    .method.GET { background: rgba(59, 130, 246, 0.2); color: #60a5fa; border: 1px solid #3b82f6; }
    .method.POST { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid #10b981; }
    .method.PATCH { background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid #f59e0b; }
    .method.DELETE { background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid #ef4444; }
    .path { font-family: monospace; font-size: 0.95rem; font-weight: 600; color: #fff; }
    .summary { color: var(--muted); font-size: 0.9rem; margin-left: auto; }
    .details {
      padding: 1.25rem;
      border-top: 1px solid var(--border);
      background: var(--code-bg);
      font-size: 0.88rem;
    }
    pre {
      background: #09090b;
      padding: 1rem;
      border-radius: 6px;
      border: 1px solid var(--border);
      overflow-x: auto;
      font-size: 0.85rem;
    }
    a.raw-link {
      color: var(--accent);
      text-decoration: none;
      font-size: 0.9rem;
    }
    a.raw-link:hover { text-decoration: underline; }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>Dogfood 2026 Portal API Documentation</h1>
      <div style="font-size: 0.85rem; color: var(--muted); margin-top: 0.25rem;">OpenAPI 3.1.0 Contract & Local Interactive Reference</div>
    </div>
    <div>
      <a href="/api/v1/schema.json" class="raw-link" target="_blank">Download Raw OpenAPI Schema (JSON) &rarr;</a>
    </div>
  </header>

  <main>
    <div class="intro">
      <h2 style="margin-top: 0; font-size: 1.15rem;">Authentication & Idempotency Contracts</h2>
      <p style="margin-bottom: 0.5rem; color: var(--muted);">
        All API requests support either active cookie-backed session authentication or scoped Bearer API keys:
      </p>
      <ul style="color: var(--muted); margin-top: 0;">
        <li><strong>Bearer Auth</strong>: <code>Authorization: Bearer dg_live_...</code>. Keys are scoped to a specific event with granular privileges (<code>event:read</code>, <code>submission:write</code>, <code>judging:write</code>, <code>results:publish</code>, <code>community:moderate</code>, etc.).</li>
        <li><strong>Role/Scope Intersection</strong>: Effective access is strictly the intersection of the key scope and the user's current event role. An API key scope cannot elevate a participant into an organiser.</li>
        <li><strong>Idempotency</strong>: Safe mutation retries supported on POST/PATCH via header <code>Idempotency-Key: &lt;uuid-or-token&gt;</code>. Cached responses are returned for matching requests; duplicate keys with different payloads return <code>409 Conflict</code>.</li>
      </ul>
    </div>

    <h2 style="font-size: 1.1rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 1rem;">Available Endpoints</h2>

    <div id="endpoints-container">
      <!-- Generated client-side from schema -->
    </div>
  </main>

  <script>
    fetch('/api/v1/schema.json')
      .then(res => res.json())
      .then(schema => {
        const container = document.getElementById('endpoints-container');
        for (const [path, methods] of Object.entries(schema.paths)) {
          for (const [method, op] of Object.entries(methods)) {
            const ep = document.createElement('div');
            ep.className = 'endpoint';
            
            const header = document.createElement('div');
            header.className = 'endpoint-header';
            header.innerHTML = `
              <span class="method ${method.toUpperCase()}">${method.toUpperCase()}</span>
              <span class="path">${path}</span>
              <span class="summary">${op.summary || ''}</span>
            `;

            const details = document.createElement('div');
            details.className = 'details';
            details.style.display = 'none';
            details.innerHTML = `
              <p><strong>Tag:</strong> ${(op.tags || []).join(', ')}</p>
              <pre>${JSON.stringify(op, null, 2)}</pre>
            `;

            header.addEventListener('click', () => {
              details.style.display = details.style.display === 'none' ? 'block' : 'none';
            });

            ep.appendChild(header);
            ep.appendChild(details);
            container.appendChild(ep);
          }
        }
      });
  </script>
</body>
</html>"""
