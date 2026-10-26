# claude-profile demo. Uses its own data folder (~/.claude-profile-demo) and demo
# project folders (~/claude-profile-demo); your real profiles are not touched.
# Cleanup afterwards:  Remove-Item -Recurse -Force ~/.claude-profile-demo, ~/claude-profile-demo

param([switch]$NoTab)
$ErrorActionPreference = 'Continue'
$env:CLAUDE_PROFILE_HOME = Join-Path $HOME '.claude-profile-demo'
$demo = Join-Path $HOME 'claude-profile-demo'

function Step([string]$Title, [string]$Why) {
    Write-Host ''
    Write-Host ('=' * 78) -ForegroundColor DarkMagenta
    Write-Host "  $Title" -ForegroundColor Magenta
    if ($Why) { Write-Host "  $Why" -ForegroundColor DarkGray }
    Write-Host ('=' * 78) -ForegroundColor DarkMagenta
    Start-Sleep -Milliseconds 1500
}

function Run([string]$Cmd) {
    Write-Host ''
    Write-Host "PS $((Get-Location).Path.Replace($HOME, '~'))> " -ForegroundColor DarkGray -NoNewline
    Write-Host $Cmd -ForegroundColor Yellow
    Start-Sleep -Milliseconds 700
    Invoke-Expression $Cmd
    Start-Sleep -Milliseconds 1200
}

# Fresh start
Remove-Item -Recurse -Force $env:CLAUDE_PROFILE_HOME, $demo -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$demo\acme-repo\src", "$demo\globex-repo", "$demo\initech-repo" | Out-Null

Step '1. Three customers, three different AI platforms' 'Keys below are fake demo values. Each customer folder gets bound to its profile.'
Set-Location "$demo\acme-repo"
Run 'claude-profile add acme --provider deepseek --label "ACME Corp" --key sk-demo-not-real --bind'
Set-Location "$demo\globex-repo"
Run 'claude-profile add globex --provider claude --label "Globex (Enterprise)" --set org=00000000-demo-0000-0000-000000000000 --bind'
Set-Location "$demo\initech-repo"
Run 'claude-profile add initech --provider openrouter --label "Initech (OpenAI)" --set model=openai/gpt-5 --key sk-or-demo-not-real --bind'

Step '2. All profiles at a glance' 'default = your normal ~/.claude setup, always there and untouched.'
Run 'claude-profile list'

Step '3. The folder decides the customer' 'No switching needed: cd into a customer repo and claude-profile knows who you work for.'
Set-Location "$demo\acme-repo\src"
Run 'claude-profile status'
Set-Location "$demo\globex-repo"
Run 'claude-profile status'
Set-Location $demo
Run 'claude-profile status'

Step '4. MCP servers are sealed per customer' 'ACME gets a Jira server, Globex a Confluence server. Neither can see the other.'
Set-Location "$demo\acme-repo"
Run 'claude-profile mcp add --scope user acme-jira -- npx -y @demo/acme-jira-mcp'
Set-Location "$demo\globex-repo"
Run 'claude-profile mcp add --scope user globex-confluence -- npx -y @demo/globex-confluence-mcp'
Write-Host ''
Write-Host '  Now ask each customer for the OTHER customer''s server:' -ForegroundColor Cyan
Set-Location "$demo\acme-repo"
Run 'claude-profile mcp get globex-confluence'
Set-Location "$demo\globex-repo"
Run 'claude-profile mcp get acme-jira'

Step '5. Guard rail: wrong customer in the wrong folder' 'Starting ACME inside the Globex repo is stopped (it would ask y/N interactively).'
Set-Location "$demo\globex-repo"
Run '$null | claude-profile acme'

Step '6. API keys are encrypted' 'On Windows with DPAPI: only your Windows user on this PC can decrypt them.'
Run '(Get-Content "$env:CLAUDE_PROFILE_HOME\profiles\acme\profile.json" | ConvertFrom-Json).secret.data.Substring(0, 60) + "..."'

Step '7. Each launch gets only its own customer''s settings' 'Claude Code starts with ACME''s config folder and DeepSeek endpoint (here just printing its version).'
Set-Location "$demo\acme-repo"
Run 'claude-profile acme --version'

Step '8. Claude Desktop' 'Desktop switches with a restart: quit it, run "claude-profile desktop acme", start it. Showing status only.'
Run 'claude-profile desktop'

Step '9. Setup check'
Run 'claude-profile doctor'

Step '10. A live session: Globex in its own colored Windows Terminal tab' 'Brand-new profile, so Claude asks for the Globex sign-in: nothing from your own account leaks in.'
if (-not $NoTab) { Run 'claude-profile tab globex' }

Write-Host ''
Write-Host 'Demo finished. Play with the demo profiles yourself, e.g.:' -ForegroundColor Green
Write-Host '  claude-profile --home ~/.claude-profile-demo list'
Write-Host '  claude-profile --home ~/.claude-profile-demo menu'
Write-Host 'Remove the demo afterwards:' -ForegroundColor Green
Write-Host '  Remove-Item -Recurse -Force ~/.claude-profile-demo, ~/claude-profile-demo'
