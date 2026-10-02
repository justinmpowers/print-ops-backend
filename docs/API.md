# 3D Print Shop Manager - REST API Reference

Complete API documentation for all backend endpoints.

## Base URL

```
http://localhost:5000/api
```

All requests require JWT Bearer token in Authorization header:

```
Authorization: Bearer {jwt_token}
```

## Authentication Endpoints

### GET /auth/login
Get OAuth login URL and code verifier for Etsy authentication.

**Response:**
```json
{
  "auth_url": "https://www.etsy.com/oauth/authorize?...",
  "code_verifier": "random_code_verifier_string"
}
```

### POST /auth/callback
Handle OAuth callback with authorization code.

**Request:**
```json
{
  "code": "auth_code_from_etsy",
  "code_verifier": "code_verifier_from_login"
}
```

**Response:**
```json
{
  "token": "jwt_token_here",
  "user": {
    "id": 1,
    "etsy_user_id": "user_id",
    "username": "shop_name",
    "first_name": "Owner Name",
    "shop_id": "shop_id",
    "shop_name": "My 3D Shop"
  }
}
```

### POST /auth/logout
Logout current user.

**Response:**
```json
{
  "message": "Logged out successfully"
}
```

### GET /auth/user
Get current authenticated user info.

**Response:**
```json
{
  "id": 1,
  "etsy_user_id": "user_id",
  "username": "shop_name",
  "first_name": "Owner Name",
  "shop_id": "shop_id",
  "shop_name": "My 3D Shop",
  "created_at": "2025-01-01T00:00:00",
  "updated_at": "2025-01-01T00:00:00"
}
```

## Order Management

### GET /orders
List all orders with filtering.

**Query Parameters:**
- `status` - Filter by order status
- `production_status` - Filter by production status
- `start_date` - Filter from date
- `end_date` - Filter to date
- `limit` - Results per page (default: 50)
- `offset` - Pagination offset (default: 0)

**Response:**
```json
{
  "orders": [
    {
      "id": 1,
      "etsy_order_id": "123456789",
      "buyer_name": "John Doe",
      "buyer_email": "john@example.com",
      "status": "paid",
      "production_status": "in_progress",
      "total_amount": 49.99,
      "currency": "USD",
      "created_at": "2025-01-01T00:00:00",
      "items": [
        {
          "id": 1,
          "title": "Custom Bracket",
          "quantity": 1,
          "price": 49.99
        }
      ]
    }
  ],
  "total": 150,
  "page": 1
}
```

### GET /orders/:id
Get specific order details.

**Response:** Single order object (see above)

### PUT /orders/:id
Update order status and production details.

**Request:**
```json
{
  "status": "shipped",
  "production_status": "completed",
  "notes": "Completed and ready to ship"
}
```

**Response:** Updated order object

### POST /orders/sync
Sync new orders from Etsy.

**Response:**
```json
{
  "message": "Synced 5 new orders",
  "synced_count": 5,
  "total_orders": 150
}
```

### POST /orders/:id/notes
Add internal note to order.

**Request:**
```json
{
  "content": "Printed successfully, no defects"
}
```

**Response:**
```json
{
  "id": 1,
  "order_id": 1,
  "content": "Printed successfully",
  "created_at": "2025-01-01T00:00:00"
}
```

### GET /orders/:id/notes
Get all notes for order.

**Response:**
```json
[
  {
    "id": 1,
    "content": "Note content",
    "created_at": "2025-01-01T00:00:00"
  }
]
```

### POST /orders/:id/communications
Log customer communication.

**Request:**
```json
{
  "message": "Customer asked about delivery date",
  "direction": "inbound",
  "channel": "etsy_message"
}
```

## Filament Inventory

### GET /filaments
List all filaments.

**Query Parameters:**
- `material` - Filter by material type
- `color` - Filter by color
- `low_stock` - Show only low stock (true/false)

**Response:**
```json
{
  "filaments": [
    {
      "id": 1,
      "material": "PLA",
      "color": "Red",
      "initial_amount": 1000,
      "current_amount": 750,
      "cost_per_gram": 0.02,
      "low_stock_threshold": 100,
      "created_at": "2025-01-01T00:00:00",
      "updated_at": "2025-01-01T00:00:00"
    }
  ],
  "total": 15
}
```

### POST /filaments
Add new filament.

**Request:**
```json
{
  "material": "PLA",
  "color": "Blue",
  "initial_amount": 1000,
  "current_amount": 1000,
  "cost_per_gram": 0.015,
  "low_stock_threshold": 100
}
```

### PUT /filaments/:id
Update filament.

**Request:** Same as POST

### DELETE /filaments/:id
Remove filament.

**Response:**
```json
{
  "message": "Filament deleted"
}
```

## Printer Management

### GET /bambu/printers
List all printers.

**Response:**
```json
[
  {
    "id": 1,
    "name": "Main Printer",
    "type": "bambu",
    "connection_type": "bambu_cloud",
    "serial_number": "00M...",
    "status": "online",
    "current_job": "Print Job Name",
    "utilization_pct": 65,
    "created_at": "2025-01-01T00:00:00"
  }
]
```

### POST /bambu/printers
Add new printer.

**Request (Bambu Lab Cloud):**
```json
{
  "name": "Main Printer",
  "connection_type": "bambu_cloud",
  "serial_number": "00M...",
  "access_code": "access_code_from_bambu"
}
```

**Request (Bambu Lab LAN):**
```json
{
  "name": "Main Printer",
  "connection_type": "bambu_lan",
  "api_url": "http://192.168.1.100",
  "access_code": "access_code"
}
```

### GET /bambu/printers/:id/status
Get real-time printer status.

**Response:**
```json
{
  "id": 1,
  "status": "printing",
  "current_job": "Custom Bracket",
  "progress": 45,
  "nozzle_temp": 220,
  "bed_temp": 60,
  "chamber_temp": 45,
  "utilization": 65,
  "estimated_time_remaining": 1200,
  "last_updated": "2025-01-01T12:34:56"
}
```

### DELETE /bambu/printers/:id
Remove printer.

**Response:**
```json
{
  "message": "Printer deleted"
}
```

## AMS Materials

### GET /bambu/materials
List materials in all AMS slots.

**Query Parameters:**
- `printer_id` - Filter by printer

**Response:**
```json
[
  {
    "id": 1,
    "printer_id": 1,
    "slot": 1,
    "material_type": "PLA",
    "color": "#FF0000",
    "weight_grams": 950,
    "remaining_pct": 95,
    "vendor": "Bambu",
    "cost_per_kg": 20,
    "loaded_at": "2025-01-01T00:00:00",
    "last_synced": "2025-01-01T12:34:56"
  }
]
```

### PUT /bambu/materials/:id
Update material info.

**Request:**
```json
{
  "remaining_pct": 85,
  "vendor": "Prusament",
  "cost_per_kg": 25
}
```

## Print Scheduling

### POST /bambu/scheduled-prints
Schedule a print job.

**Request:**
```json
{
  "printer_id": 1,
  "job_name": "Custom Bracket",
  "file_name": "bracket.3mf",
  "material_type": "PLA",
  "material_slot": 1,
  "estimated_duration_minutes": 240,
  "priority": 1
}
```

**Response:**
```json
{
  "id": 1,
  "printer_id": 1,
  "job_name": "Custom Bracket",
  "status": "queued",
  "priority": 1,
  "scheduled_start": "2025-01-01T14:00:00",
  "created_at": "2025-01-01T12:00:00"
}
```

### GET /bambu/scheduled-prints
List scheduled prints.

**Query Parameters:**
- `printer_id` - Filter by printer
- `status` - Filter by status (queued, scheduled, started, completed, failed)

### GET /bambu/scheduled-prints/:id/queue
Get print queue for printer.

**Response:**
```json
[
  {
    "id": 1,
    "job_name": "Job 1",
    "status": "queued",
    "priority": 1,
    "estimated_duration": 240
  }
]
```

### PUT /bambu/scheduled-prints/:id
Update print status.

**Request:**
```json
{
  "status": "started"
}
```

### DELETE /bambu/scheduled-prints/:id
Cancel scheduled print.

## Notifications

### GET /bambu/notifications
Get notification settings for printer.

**Query Parameters:**
- `printer_id` - Get settings for printer

**Response:**
```json
{
  "id": 1,
  "printer_id": 1,
  "notify_print_start": true,
  "notify_print_complete": true,
  "notify_print_failed": true,
  "notify_material_change": true,
  "notify_maintenance": false,
  "email_enabled": false,
  "webhook_url": "https://example.com/webhook"
}
```

### PUT /bambu/notifications/:id
Update notification settings.

**Request:**
```json
{
  "notify_print_complete": true,
  "email_enabled": true,
  "webhook_url": "https://example.com/webhook"
}
```

## Devices

Unattended devices (for example a PrintHub board sitting next to the printers) authenticate
with a device key instead of a user session. Create the key while logged in, then put it in
the device's config. The device sends it in an `X-Device-Key` header.

### POST /devices
Create a device key. The raw key is returned **once**; only its hash is stored.

**Request:**
```json
{ "name": "PrintHub" }
```

**Response (201):**
```json
{
  "id": 1,
  "name": "PrintHub",
  "key": "pok_...",
  "key_prefix": "pok_AbCdEf",
  "last_used_at": null,
  "revoked_at": null,
  "created_at": "2026-10-02T03:40:00"
}
```

### GET /devices
List device keys (without the raw key).

### DELETE /devices/:id
Revoke a device key. The device's requests are rejected from then on.

### GET /device/ping
Device-authenticated. Lets a device check its key.

**Response:** `{ "ok": true, "device": "PrintHub" }`

### POST /device/printer-events
Device-authenticated. Records a print starting, finishing or failing.

**Request:**
```json
{
  "printer": { "name": "X1 Carbon", "serial_number": "00M00A000000000" },
  "event": "started",
  "job_name": "benchy",
  "message": "optional failure reason"
}
```

- `event` is `started`, `finished` or `failed`.
- The printer is matched by connection serial number first, then by name (case-insensitive).
  An unmatched printer returns `404`.
- `started` claims a queued/scheduled print on that printer whose job or file name matches,
  otherwise creates a scheduled-print row with status `started`.
- `finished` / `failed` close the open print (status `completed` / `failed`) and set the
  printer back to `IDLE`.

**Response:** `{ "printer_id": 1, "print": { ...scheduled print... } }`

---

## Analytics

### GET /analytics/dashboard
Get dashboard metrics.

**Response:**
```json
{
  "total_orders": 150,
  "orders_this_month": 25,
  "revenue_this_month": 1249.50,
  "average_order_value": 49.98,
  "total_prints": 300,
  "prints_completed": 295,
  "print_success_rate": 98.3,
  "filament_used_kg": 45.5,
  "materials_in_stock": 12,
  "low_stock_alerts": 2
}
```

### GET /analytics/revenue
Get revenue reports.

**Query Parameters:**
- `start_date` - Report start date
- `end_date` - Report end date
- `period` - daily, weekly, monthly

**Response:**
```json
{
  "period": "monthly",
  "data": [
    {
      "date": "2025-01-01",
      "revenue": 249.50,
      "order_count": 5,
      "average_order": 49.90
    }
  ],
  "total_revenue": 1249.50,
  "total_orders": 25
}
```

## Error Responses

All errors follow this format:

```json
{
  "error": "Error message",
  "status": 400
}
```

### Common Status Codes

- `200` - Success
- `201` - Created
- `400` - Bad Request
- `401` - Unauthorized
- `403` - Forbidden
- `404` - Not Found
- `409` - Conflict
- `500` - Server Error

### Common Errors

- `401 Unauthorized` - Invalid or missing JWT token
- `403 Forbidden` - User doesn't have access
- `404 Not Found` - Resource doesn't exist
- `400 Bad Request` - Invalid parameters

## Rate Limiting

API rate limits:
- **Authenticated requests**: 1000 per hour
- **Etsy sync**: 5 per minute
- **Print operations**: 100 per minute

Rate limit headers:
```
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 999
X-RateLimit-Reset: 1234567890
```

## Pagination

List endpoints support pagination:

```
GET /api/orders?limit=50&offset=0
```

Response includes pagination info:
```json
{
  "data": [...],
  "total": 500,
  "limit": 50,
  "offset": 0,
  "page": 1,
  "pages": 10
}
```
