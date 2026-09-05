# Drives the whole demo in order, hands-free, for a single screen recording take.
#
#   .\record_demo.ps1              timed pauses, no keyboard needed while recording
#   .\record_demo.ps1 -Pause       waits for Enter between sections
#
# Each section sets the planner through real environment variables rather than editing
# .env, because gymapp.config.load_dotenv uses setdefault - a real variable wins over
# the file. So nothing is left half-edited if the recording is interrupted.

param(
    [switch]$Pause,
    [int]$Gap = 6
)

$ErrorActionPreference = 'Continue'
$HOSTED = 'core.services.recovery_agent.OpenAIToolCallingClient'
# Named explicitly rather than blanking the variable. Assigning '' in PowerShell
# *removes* the variable, and load_dotenv then supplies the hosted planner from .env -
# which silently ran the deterministic clip on the hosted model.
$OFFLINE = 'core.services.recovery_agent.HeuristicRecoveryPlanner'

$script:Clock = [System.Diagnostics.Stopwatch]::StartNew()

function Elapsed {
    $t = $script:Clock.Elapsed
    return ('{0:mm\:ss}' -f $t)
}

function Banner {
    param([string]$Number, [string]$Title, [string]$Says)
    Write-Host ""
    Write-Host ("=" * 100) -ForegroundColor DarkCyan
    # The elapsed stamp is on screen in the footage, so editing can cut to an exact
    # frame instead of anyone guessing where a section began.
    Write-Host "  [T+$(Elapsed)]  CLIP $Number   $Title" -ForegroundColor Cyan
    Write-Host ("=" * 100) -ForegroundColor DarkCyan
    Write-Host "  narrate: $Says" -ForegroundColor DarkGray
    Write-Host ""
    Start-Sleep -Seconds 2
}

function Hold {
    Write-Host ""
    Write-Host "  [T+$(Elapsed)]  clip ended" -ForegroundColor DarkGray
    if ($Pause) {
        Write-Host "  -- press Enter for the next clip --" -ForegroundColor DarkGray
        Read-Host | Out-Null
    } else {
        Write-Host "  -- next clip in $Gap seconds --" -ForegroundColor DarkGray
        Start-Sleep -Seconds $Gap
    }
}

Clear-Host
Write-Host ""
Write-Host "  AI REVENUE RECOVERY AGENT" -ForegroundColor White
Write-Host "  Razorpay AI Buildathon - Track 03" -ForegroundColor DarkGray
Write-Host ""
Start-Sleep -Seconds 3

# ----------------------------------------------------------------------------------
Banner "1 / 4" "MEASURED MONEY RECOVERED ACROSS A BATCH" `
    "50 overdue invoices. The recovered figure is read back from the ledger."

$env:RECOVERY_LLM_CLIENT = $OFFLINE
python manage.py run_recovery_batch --synthetic-data --rounds 4 2>$null
Hold

# ----------------------------------------------------------------------------------
Banner "2 / 4" "THE SAME AGENT, PLANNED BY A REAL MODEL" `
    "Planner reads openai. No fallback line, so every decision came from the model."

$env:RECOVERY_LLM_CLIENT = $HOSTED
python manage.py run_recovery_batch --synthetic-data --count 8 --rounds 4 --reasoning 2>$null
Hold

# ----------------------------------------------------------------------------------
# Note for narration: do NOT promise the model will refuse. Observed runs have gone
# both ways - escalate_to_human on one, a normal tier-3 discount on another. The point
# of this clip is that the injection is *in the prompt*, not what the model does with it.
Banner "3 / 4" "PROMPT INJECTION REACHES THE PLANNER" `
    "Look at member_name in the JSON. Untrusted text, inside the prompt. Whatever the model does next is not the safeguard."

$env:RECOVERY_LLM_CLIENT = $HOSTED
python manage.py demo_prompt_injection --show-prompt 2>$null
Hold

# ----------------------------------------------------------------------------------
Banner "4 / 4" "THE MODEL OBEYS THE INJECTION AND STILL GETS NOTHING" `
    "Cap clamps 90 to 20. Tenant boundary refuses. Other tenant untouched."

python manage.py demo_prompt_injection --force-compliance 2>$null
$injectionExit = $LASTEXITCODE

# ----------------------------------------------------------------------------------
Write-Host ""
Write-Host ("=" * 100) -ForegroundColor DarkCyan
if ($injectionExit -eq 0) {
    Write-Host "  [T+$(Elapsed)]  All four clips done. Injection demo exited 0: nothing leaked." -ForegroundColor Green
} else {
    Write-Host "  [T+$(Elapsed)]  Injection demo exited $injectionExit - a guardrail did NOT hold. Do not ship this take." -ForegroundColor Red
}
Write-Host ("=" * 100) -ForegroundColor DarkCyan
Write-Host ""
$script:Clock.Stop()

$env:RECOVERY_LLM_CLIENT = $null
