# Amazon Connect Resource Mapper

**Predict API quota breaches before they happen — not after your phones go silent.**

## Why This Exists

Amazon Connect enforces API rate limits on every call your contact flows make. There's no built-in tool that shows you how your phone numbers, contact flows, Lambda functions, and API quotas connect — or what happens to those limits when you scale.

Teams discover throttling *after* phones start dropping calls. Quota increases take 3-5 business days. The migration was yesterday.

**Connect Resource Mapper** scans your instance in under 5 minutes and tells you exactly how many phone numbers you can safely add before hitting limits — and which limits to increase first.

## Who This Is For

- Enterprise contact center teams with **1,000+ phone numbers**
- Teams **migrating** from legacy platforms to Connect
- Teams **scaling** existing Connect deployments
- Anyone who's been surprised by `ThrottlingException` in production

## FAQ

**Does it change anything in my AWS account?**
No. Read-only. Zero mutations. It only calls List/Describe/Get APIs.

**How long does it take to run?**
2-5 minutes depending on instance size.

**Can I share the dashboard with my team?**
Yes. The HTML file is self-contained — no server, no install, opens in any browser offline.

**What's the #1 thing it can't do?**
The Connect API doesn't expose which Contact Flow a phone number routes to. The tool maps Numbers → TDGs and Flows → Lambdas, but the middle link (TDG → Flow) requires a console export from your team.

---

## What It Does

```
Phone Numbers  →  Contact Flows  →  Lambda Functions  →  API Calls  →  Quota Limits
   (45,000)          (14)              (160)             (18/call)      (2-60 TPS)
```

1. **Maps your topology** — Discovers every phone number, which Traffic Distribution Group (TDG) it belongs to, which contact flow handles it, and which Lambda functions that flow invokes.

2. **Measures current usage** — Pulls 7 days of CloudWatch API usage metrics to determine your current Transactions Per Second (TPS) for each Connect API.

3. **Calculates headroom** — Compares your current peak TPS against your applied quota limits to show exactly how much room you have.

4. **Predicts migration impact** — Uses a formula-based model to calculate how many additional TPS each migration wave will add, per API, so you can file quota increases *before* you migrate, not after.

## Prerequisites

- **Python 3.9+** (check with `python3 --version`)
- **boto3** (`pip install boto3`)
- **AWS credentials** with read-only Connect access (see [Required IAM Permissions](#required-iam-permissions))
- **Your Connect Instance ID** — find it in the [Connect console](https://console.aws.amazon.com/connect/) URL or run:
  ```bash
  aws connect list-instances --query 'InstanceSummaryList[].{Id:Id,Name:InstanceAlias}' --output table
  ```

## Quick Start — First Result in 60 Seconds

```bash
# 1. Install
pip install boto3

# 2. Run (replace with your instance ID)
python connect-resource-mapper.py \
  --instance-id YOUR_INSTANCE_ID \
  --region us-east-1 \
  --output-dir ./output

# 3. Open the dashboard
open ./output/connect-dashboard.html
```

That's it. You'll see something like this in your terminal:

```
14:02:31 [INFO] ============================================================
14:02:31 [INFO]   Amazon Connect Resource Mapper
14:02:31 [INFO]   Instance: 587c546e-2328-4c36-baa2-37eaf4749631
14:02:31 [INFO]   Region:   us-east-1
14:02:31 [INFO] ============================================================
14:02:31 [INFO] Collecting phone numbers...
14:02:33 [INFO] Found 1247 phone numbers.
14:02:33 [INFO] Collecting Traffic Distribution Groups...
14:02:34 [INFO] Found 4 TDGs.
14:02:34 [INFO] Collecting contact flows...
14:02:35 [INFO] Found 23 contact flows.
14:02:35 [INFO] Describing each flow to extract Lambda mappings...
14:03:02 [INFO] Described 23 flows.
14:03:02 [INFO] Collecting Lambda functions...
14:03:04 [INFO] Found 47 Connect-associated Lambdas.
14:03:04 [INFO] Collecting Lex bots...
14:03:05 [INFO] Found 3 Lex bots via Connect.
14:03:05 [INFO] Collecting service quotas...
14:03:06 [INFO] Found 64 service quotas.
14:03:06 [INFO] Collecting CloudWatch usage metrics...
14:03:12 [INFO] Collected metrics for 11 APIs.
14:03:12 [INFO] Building quota impact model...
14:03:12 [INFO] ────────────────────────────────────────────────────────────
14:03:12 [INFO] Summary:
14:03:12 [INFO]   Phone numbers: 1247
14:03:12 [INFO]   Contact flows: 23
14:03:12 [INFO]   Lambdas: 47
14:03:12 [INFO]   Provisioned Concurrency: 0
14:03:12 [INFO]   Quotas > 70% utilized: 2
14:03:12 [INFO] ============================================================
```

## Example Output

After running, you get three files:

### `connect-resource-map.json` (resource graph)

```json
{
  "_metadata": {
    "tool": "connect-resource-mapper",
    "version": "1.0.0",
    "data_provenance": "All values are direct API responses from AWS..."
  },
  "instance_id": "587c546e-2328-4c36-baa2-37eaf4749631",
  "region": "us-east-1",
  "phone_numbers_count": 1247,
  "phone_numbers": [ ... ],
  "tdgs": [ ... ],
  "contact_flows": [
    {
      "Id": "abc123",
      "Name": "Main-Entry-Flow",
      "lambdas_invoked": [
        "arn:aws:lambda:us-east-1:123456789012:function:entry-lookup",
        "arn:aws:lambda:us-east-1:123456789012:function:routing"
      ]
    }
  ],
  "lambda_functions": [ ... ],
  "quotas": [ ... ],
  "usage_metrics": {
    "GetContactAttributes": {
      "daily_values": [748769, 681775, 695827, 690806, 184864, 72716, 772314],
      "avg_daily": 692439,
      "peak_daily": 772314,
      "peak_tps_estimate": 26.81
    }
  }
}
```

### `connect-quota-impact-model.json` (predictive model)

```json
{
  "_metadata": {
    "data_provenance": "quota_headroom.peak_tps calculated as max(7-day daily sum) / 28800..."
  },
  "quota_headroom": {
    "L-5AF7EB96": {
      "name": "Rate of GetContactAttributes API requests",
      "limit": 60.0,
      "peak_tps": 26.81,
      "utilization_pct": 44.7,
      "headroom_tps": 33.19
    }
  },
  "migration_impact_formulas": {
    "per_migration_wave_of_N_numbers": {
      "formula": "N * avg_contacts_per_day_per_number / 28800 * apis_per_contact",
      "action": "Compare to headroom_tps for each API. File SLI for any exceeding 70%."
    }
  },
  "summary": {
    "total_numbers": 1247,
    "total_flows": 23,
    "quotas_above_70_pct": 2
  }
}
```

### `connect-dashboard.html`

A self-contained interactive dashboard. Open in any browser — no server needed, works offline.

---

> ⚠️ **Key Limitation:** The Connect API does not expose which Contact Flow a phone number is routed to. The tool maps Numbers → TDGs and Flows → Lambdas, but the TDG → Flow link requires a console export from your team. See [Limitations](#limitations) for details and workarounds.

---

## Understanding the Model

### The Formula

When you migrate `N` phone numbers to a contact flow:

```
Additional TPS = N × contacts_per_day × API_calls_per_contact ÷ 28,800
```

Where:
- `contacts_per_day` = average inbound contacts per number per day (typically 10-20)
- `API_calls_per_contact` = total Connect API calls made during one contact (typically 15-22)
- `28,800` = seconds in an 8-hour business day (peak period)

### Example

Migrating 500 numbers with 15 contacts/day and 18 API calls/contact:

```
500 × 15 × 18 ÷ 28,800 = 4.7 additional TPS across all APIs
```

If `GetContactAttributes` is currently at 52/60 TPS (87%), and it handles ~40% of all API traffic, that's +1.9 TPS → 53.9/60 = 90%.

Two more waves of 500 numbers each → **quota breach**.

### When to File a Quota Increase

File a Service Limit Increase (SLI) through AWS Support **before** any migration wave that would push an API above **70% utilization**. This gives AWS time to provision capacity before your traffic arrives.

## Required IAM Permissions

Create a policy with these read-only permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "connect:ListContactFlows",
        "connect:DescribeContactFlow",
        "connect:ListPhoneNumbersV2",
        "connect:ListQueues",
        "connect:ListRoutingProfiles",
        "connect:ListLambdaFunctions",
        "connect:ListTrafficDistributionGroups",
        "connect:DescribeTrafficDistributionGroup",
        "connect:GetTrafficDistribution",
        "lambda:GetFunction",
        "lambda:ListProvisionedConcurrencyConfigs",
        "lexv2:ListBots",
        "lexv2:ListBotAliases",
        "servicequotas:ListServiceQuotas",
        "cloudwatch:GetMetricData"
      ],
      "Resource": "*"
    }
  ]
}
```

**This tool makes zero changes to your AWS account.** It only reads.

## CLI Options

| Flag | Description | Default |
|------|-------------|---------|
| `--instance-id` | Connect Instance ID (required) | — |
| `--region` | AWS region | `us-east-1` |
| `--output-dir` | Where to write output files | Current directory |
| `--skip-flow-content` | Skip detailed flow inspection (faster, but no Lambda mapping) | `false` |
| `--verbose` / `-v` | Enable debug logging | `false` |

## Running Tests

```bash
pip install pytest
python -m pytest test-connect-resource-mapper.py -v
```

Tests run without AWS credentials — they use mocked API responses.

## How It Works (Architecture)

```
┌─────────────────────────────────────────────────────────────────┐
│                     COLLECTION PHASE                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  Layer 1: Phone Numbers & TDGs                                   │
│    └─ ListPhoneNumbersV2 (paginated)                             │
│    └─ ListTrafficDistributionGroups                              │
│    └─ GetTrafficDistribution (per TDG)                           │
│                                                                   │
│  Layer 2: Contact Flows                                           │
│    └─ ListContactFlows (paginated)                               │
│    └─ DescribeContactFlow (per flow → extracts Lambda ARNs)      │
│                                                                   │
│  Layer 3: Lambda Functions                                        │
│    └─ ListLambdaFunctions (Connect-associated)                   │
│    └─ GetFunction (per Lambda)                                    │
│    └─ ListProvisionedConcurrencyConfigs (per Lambda)              │
│                                                                   │
│  Layer 4: Lex Bots                                                │
│    └─ ListBots (Connect-associated or account-level)             │
│                                                                   │
│  Layer 5: Quotas & Usage                                          │
│    └─ ListServiceQuotas (paginated, service: connect)            │
│    └─ GetMetricData (CloudWatch, 7-day lookback, per API)        │
│                                                                   │
├─────────────────────────────────────────────────────────────────┤
│                     MODEL PHASE                                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  Build:                                                           │
│    • Flow → Lambda map (which Lambdas each flow invokes)         │
│    • TDG → Number distribution (numbers per TDG by type)         │
│    • Quota headroom (limit - peak TPS per API)                   │
│    • Migration impact formulas                                    │
│                                                                   │
├─────────────────────────────────────────────────────────────────┤
│                     OUTPUT PHASE                                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  • connect-resource-map.json (full graph)                         │
│  • connect-quota-impact-model.json (predictive model)            │
│  • connect-dashboard.html (interactive calculator)               │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
```

## Key Concepts for Non-Connect Users

| Term | What It Is |
|------|------------|
| **TDG** (Traffic Distribution Group) | Routes incoming calls to different instances/regions. Think of it as a load balancer for phone calls. |
| **Contact Flow** | The logic that runs when a call arrives — like a flowchart that checks business hours, invokes AI bots, routes to agents. |
| **Lambda Function** | Custom code that runs during a call — looks up customer data, checks queues, makes routing decisions. |
| **TPS** (Transactions Per Second) | How many API calls per second you're making. Every Lambda invocation triggers 3-5 Connect API calls. |
| **Quota / Service Limit** | The maximum TPS AWS allows for each API. Default is usually 2 TPS. You can request increases. |
| **SLI** (Service Limit Increase) | A request to AWS Support to raise your quota. Takes 1-5 business days. |
| **DID** (Direct Inward Dialing) | A local phone number (has an area code matching a geographic region). |
| **TFN** (Toll-Free Number) | An 800/888/877 number that callers don't pay to reach. |
| **Provisioned Concurrency** | Pre-warmed Lambda instances that eliminate cold start delays. Zero means every call risks 1-2 second delays. |

## Limitations

- **Phone Number → Contact Flow mapping** is not directly available via API. The tool maps Numbers → TDGs and Flows → Lambdas, but the TDG → Flow link requires either a console export or a DynamoDB lookup table maintained by your team.
- **Lex bot inventory** requires console access if `connect:ListBots` is not available in your region.
- **Usage metrics** reflect the past 7 days. If your traffic is seasonal, run the tool during peak periods for accurate projections.
- **TPS estimates** assume an 8-hour business day distribution. If your contact center operates 24/7, divide by 86,400 instead.

## Contributing

Pull requests welcome. Please run `python -m pytest test-connect-resource-mapper.py -v` before submitting.

## License

MIT
