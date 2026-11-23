# Printer Setup Guide

Complete guide to setting up and configuring supported 3D printers.

## Supported Printers

### Bambu Lab (Recommended)
- **P1 Series** - P1, P1P, P1S
- **X1 Series** - X1, X1 Carbon, X1E

### Klipper / Moonraker
- **Snapmaker U1** and other Klipper printers (Voron, Creality K1, ...). See [Snapmaker U1 Setup](#snapmaker-u1-setup).

### AnkerMake
- **M5 / M5C**, through PrintHub. See [AnkerMake M5 Setup](#ankermake-m5-setup).

### Future Support
- Prusa i3 MK3S+
- Creality Ender 3 Pro
- Anycubic i3 Mega

## Snapmaker U1 Setup

The U1 runs Klipper with Moonraker, which serves a status API on port 7125.

1. Give the printer a fixed IP (a DHCP reservation on your router).
2. Check `http://<printer-ip>:7125/server/info` opens from a machine on the same network.
3. In PrintOps, add a printer with connection type **Klipper / Moonraker** and API URL
   `http://<printer-ip>:7125`. Leave the API key empty unless you turned on Moonraker authorization.

The backend polls `/printer/objects/query` for print state, progress, layers and temperatures.
The nozzle temperature shown is the active toolhead's (the U1 has four). If the PrintOps backend
runs outside your home network it can't reach the printer; add the U1 to PrintHub instead and its
live status is pushed to PrintOps.

## AnkerMake M5 Setup

The M5 has no local status API: it reports only to Anker's cloud, with an encrypted payload.
PrintOps can't poll it, so status comes from PrintHub, which reads it through ankerctl
(see the PrintHub README).

1. In PrintOps, add a printer with connection type **AnkerMake (via PrintHub)**. No URL or key is needed.
2. In PrintHub's `config.yaml`, add it under `ankermake:` with **the same name** as in PrintOps.
3. Within about 30 seconds the printer page shows "Live via PrintHub" with its state.

Print start/finish/fail events are recorded the same way as for other PrintHub printers.

## Bambu Lab Setup

### Prerequisites

1. **Bambu Lab Printer** - Connected to your WiFi
2. **Bambu Studio Account** - Free account at https://bambulab.com
3. **Printer Access Code** - Available in printer settings
4. **Connection Type Decision** - Cloud or LAN
5. **Network Access** - For LAN: same network as printer

### Getting Your Access Code

#### Method 1: From Printer Control Panel

1. Turn on printer
2. Press menu button on control panel
3. Navigate to: **Settings → LAN Only Mode → Access Code**
4. Note the 6-character code
5. This is your **access_code**

#### Method 2: From Bambu Studio

1. Open Bambu Studio
2. Go to **Preferences**
3. Look for your printer in the list
4. Click on printer to view details
5. Find **Access Code** section
6. Get the 6-character code
5. This is your **access_code**

#### Method 3: From Web Interface

1. Navigate to printer's IP address in browser: `http://192.168.X.X`
2. Go to **Settings → LAN Only Mode**
3. Find **Access Code**
4. Note the 6-character code

### Finding Your Serial Number

#### From Printer
1. Power on printer
2. Press menu
3. Go to **About**
4. Find **Serial Number**
5. Format: `00M...` (starts with 00M)

#### From Bambu Studio
1. In device list
2. Right-click printer
3. Select **Device Info**
4. Find **Serial Number**

#### From Settings Sticker
- Usually on the side or back of printer
- Also inside documentation

### Connection Methods

#### Cloud Connection (Recommended)

**Best for:**
- Remote access
- Multiple locations
- Always-on monitoring
- Easier setup

**Steps:**

1. Ensure printer is connected to WiFi
2. From application settings, add new printer:
   ```
   Name: "Main Printer" (or your choice)
   Connection Type: "Bambu Cloud"
   Serial Number: 00M... (from above)
   Access Code: 6-character code
   ```
3. Click "Connect"
4. App verifies connection
5. Printer appears in dashboard

**Settings to Enable:**

In Bambu Studio > Printer Settings:
- ✅ Enable cloud access
- ✅ Enable printer notifications
- ✅ Enable remote monitoring

**Network Requirements:**
- Internet connection (WiFi)
- Firewall allows outbound to api.bambulab.com:443
- NO port forwarding needed

**Advantages:**
- Works remotely
- More stable
- Easier troubleshooting
- Better integration

#### LAN Connection

**Best for:**
- Same network access
- Lower latency
- Offline operation
- Privacy concerns

**Steps:**

1. Find printer IP address:
   - Check WiFi router admin panel
   - Or: On printer menu → Settings → Network → IP Address
   - Or: Use network scan tool
   - Format: `192.168.X.X`

2. Verify access:
   - Open browser
   - Navigate to `http://192.168.X.X`
   - Should see printer web interface

3. From application settings, add new printer:
   ```
   Name: "Workshop Printer"
   Connection Type: "Bambu LAN"
   API URL: http://192.168.X.X
   Serial Number: printer menu → Settings → Device (or the label on the printer)
   Access Code: 6-character code
   ```
4. Click "Connect"
5. App verifies connection
6. Printer appears in dashboard

**Network Configuration:**

If on different network:
1. Use VPN to access internal network
2. Or: Configure SSH tunnel
3. Or: Use VPN on printer if available

**Firewall Settings:**
- Allow port 8883 (MQTTS — this is the only port the app actually uses; the printer does not expose an HTTP status API)
- Or disable firewall between printer and server

**Advantages:**
- Faster response
- Works offline
- Lower latency
- No cloud dependency

### Managing Multiple Printers

You can register multiple printers in the system.

**Setup:**

1. Register each printer separately with unique name
2. System maintains separate queues per printer
3. Jobs assigned based on availability

**Best Practices:**

- **Naming**: Use descriptive names
  - "Main Printer - P1S"
  - "Workshop - X1 Carbon"
  - "Testing Unit - P1P"

- **Material Setup**: 
  - Install same materials for consistency
  - Label AMS slots the same way
  - Keep inventory synchronized

- **Scheduling**:
  - Set printer availability hours
  - Configure quiet hours (no night printing)
  - Set job priority rules

### AMS (Automatic Material System) Setup

#### Loading Materials

1. **Insert Spool:**
   - Pull out AMS cartridge
   - Insert filament spool
   - Close cartridge with click sound
   - Should hear confirmation beep

2. **Load in Printer:**
   - Open AMS door on printer
   - Insert cartridge into slot 1-4
   - Close door
   - Printer auto-detects

3. **Confirm in Bambu Studio:**
   - Printer recognizes material
   - Shows material type and color
   - Displays weight

#### Material Configuration

For each slot in AMS:

1. Open printer settings
2. Find AMS section
3. For each slot, configure:
   - **Material Type**: PLA, PETG, ABS, TPU, etc.
   - **Color**: For identification
   - **Brand/Vendor**: For cost tracking
   - **Weight**: Estimated remaining grams

#### AMS Tracking

System automatically:
- Tracks material usage per print
- Updates remaining weight
- Alerts when running low
- Suggests when to reload

### Printer Status Monitoring

**Available Metrics:**

- **Status**: Online/Offline/Error
- **Current Job**: Job name if printing
- **Progress**: % complete
- **Temperatures**: 
  - Nozzle temperature
  - Bed temperature
  - Chamber temperature
- **Remaining Time**: Estimated minutes left
- **Utilization**: % of capacity used

**Dashboard Display:**

Real-time updates every 30 seconds (Cloud) or 10 seconds (LAN)

Shows:
- Printer name and type
- Status indicator (green/red)
- Current job with progress bar
- Temperature gauges
- Material information

### Notifications

#### Event Types

Configure alerts for:
- ✅ Print started
- ✅ Print completed
- ✅ Print failed
- ✅ Material change needed
- ✅ Maintenance required
- ✅ Error/warning

#### Notification Channels

**Email:**
- To shop email
- Summary format
- Configurable frequency

**Webhook:**
- Custom integration
- Real-time events
- JSON payload
- For external systems

**In-App:**
- Dashboard notifications
- History log
- Persistent until dismissed

#### Configuration

1. Go to printer settings
2. Find "Notifications"
3. Enable/disable events
4. Set delivery method
5. Configure email or webhook
6. Test notification

### Troubleshooting

#### Connection Issues

**Cloud Connection Not Working:**
1. Verify WiFi connection
2. Check internet speed
3. Verify firewall allows api.bambulab.com
4. Restart printer and WiFi
5. Re-add printer with correct code

**LAN Connection Not Working:**
1. Verify both on same network
2. Check firewall between devices
3. Verify correct IP address
4. Test with browser first
5. Check access code is correct

#### Printer Not Responding

1. Check printer power
2. Verify WiFi connection on printer
3. Restart printer (power cycle)
4. In app: "Refresh" printer status
5. Check cloud/LAN service status

#### Material Not Detected

1. Open AMS door
2. Verify filament seated properly
3. Close and reopen door
4. Wait 10 seconds for detection
5. Check material type in settings

#### Job Stuck/Not Starting

1. Check printer current status
2. Verify no active job
3. Cancel failed job
4. Restart print
5. Check error logs

#### Temperature Issues

1. Wait for thermal stabilization
2. Check if part of multi-material job
3. Monitor temperatures in real-time
4. Consider ambient temperature
5. Check nozzle/bed for debris

### Security & Access

#### Access Code Security
- 6-character code = unique access
- Change code periodically (monthly)
- Don't share publicly
- Change if someone gained access

#### Permissions
- Users with valid JWT can:
  - View printer status
  - Start approved jobs
  - Manage materials
- Admin only:
  - Add/remove printers
  - Change access codes
  - Configure notifications

#### Network Security

**For Cloud:**
- HTTPS only
- Bambu handles security
- Regular security updates

**For LAN:**
- Requires network access
- Can restrict with firewall
- Optional: password protect printer web interface
- Local network only

### Maintenance

#### Regular Checks
- Weekly: Verify online status
- Monthly: Check material levels
- Monthly: Verify temperature accuracy
- Quarterly: Clean nozzle
- Quarterly: Inspect AMS system

#### Firmware Updates
1. Update in Bambu Studio first
2. Then verify in application
3. Restart printer after update
4. Re-verify connection

#### Logs & Diagnostics
System logs:
- Printer connection events
- Job status changes
- Error messages
- Temperature data
- Material usage

## Advanced Configuration

### Custom Integration

Webhook for external systems:
```json
{
  "event": "print_completed",
  "printer_id": 1,
  "printer_name": "Main Printer",
  "job_name": "Custom Bracket",
  "duration_minutes": 240,
  "timestamp": "2025-01-01T12:34:56Z"
}
```

Receive webhooks and:
- Update external CRM
- Send customer notifications
- Trigger packaging workflow
- Update accounting system

### Batch Operations

Schedule multiple prints:
1. Create print queue
2. Set priorities
3. System manages execution
4. One job per printer
5. Automatic scheduling

### Performance Tuning

**For Cloud:**
- Check network latency
- Optimal: < 100ms
- Acceptable: < 500ms

**For LAN:**
- Optimal: < 50ms
- Check WiFi signal strength
- Consider 5GHz network

## Support & Resources

- **Bambu Lab Help**: https://support.bambulab.com
- **Printer Manual**: Included documentation
- **Community**: https://community.bambulab.com
- **API Documentation**: https://github.com/bambulab/BambuStudio

## Reference

### Model Specifications

**P1S/P1P:**
- Build plate: 256x256x256mm
- Max nozzle temp: 300°C
- Max bed temp: 100°C
- 4-slot AMS standard

**X1 Carbon:**
- Build plate: 256x256x256mm
- Max nozzle temp: 300°C
- Max bed temp: 110°C
- 4-slot AMS standard

Check specific model documentation for details.
