<#
.SYNOPSIS
    Удаляет ветки уже смерженных pull request'ов.

.DESCRIPTION
    По умолчанию НИЧЕГО НЕ УДАЛЯЕТ: печатает список веток, которые попали бы
    под удаление, и завершается. Для фактического удаления нужен ключ -Apply.

    Под удаление попадает ветка, для которой выполнены ВСЕ условия сразу:
      - она существует на GitHub;
      - у неё есть смерженный pull request;
      - у неё нет открытого pull request'а;
      - она не является веткой по умолчанию;
      - она не защищена правилами репозитория.

    Ветка без pull request'а не трогается никогда: за ней может стоять чья-то
    незаконченная работа, о которой скрипт ничего не знает.

.PARAMETER Apply
    Выполнить удаление. Без этого ключа скрипт работает только на чтение.

.PARAMETER Repo
    Репозиторий в форме "владелец/имя". По умолчанию берётся из текущей папки.

.EXAMPLE
    .\cleanup-merged-branches.ps1
    Показать, что было бы удалено. Ничего не меняет.

.EXAMPLE
    .\cleanup-merged-branches.ps1 -Apply
    Удалить найденные ветки.

.NOTES
    Постоянное решение для НОВЫХ веток — галка в настройках репозитория:
    Settings - General - Pull Requests - Automatically delete head branches.
    Этот скрипт нужен для веток, накопившихся до включения галки.
#>
param(
    [switch]$Apply,
    [string]$Repo
)

$ErrorActionPreference = 'Stop'

function Fail([string]$Message) {
    Write-Host "ОТКАЗ: $Message" -ForegroundColor Red
    exit 1
}

# --- Проверка окружения -----------------------------------------------------
# Без gh дальше идти бессмысленно, и молчать об этом нельзя: пустой результат
# читался бы как "чисто", хотя на деле проверка просто не состоялась.
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Fail 'не найден GitHub CLI (gh). Установка: https://cli.github.com'
}

& gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    Fail 'GitHub CLI не авторизован. Выполните: gh auth login'
}

if (-not $Repo) {
    $Repo = & gh repo view --json nameWithOwner --jq '.nameWithOwner' 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $Repo) {
        Fail 'не удалось определить репозиторий. Запустите из папки репозитория или укажите -Repo "владелец/имя"'
    }
}
Write-Host "Репозиторий: $Repo"

# --- Сбор данных ------------------------------------------------------------
# Каждый запрос проверяется отдельно: тихо пустой ответ здесь опаснее ошибки,
# потому что превращается в ложное "удалять нечего".

$defaultBranch = & gh api "repos/$Repo" --jq '.default_branch'
if ($LASTEXITCODE -ne 0 -or -not $defaultBranch) { Fail 'не удалось получить ветку по умолчанию' }

$branchesRaw = & gh api "repos/$Repo/branches" --paginate --jq '.[] | [.name, (.protected | tostring)] | @tsv'
if ($LASTEXITCODE -ne 0) { Fail 'не удалось получить список веток' }

$existing = @{}
foreach ($line in @($branchesRaw)) {
    if (-not $line) { continue }
    $parts = $line -split "`t"
    $existing[$parts[0]] = ($parts[1] -eq 'true')
}
if ($existing.Count -eq 0) { Fail 'список веток пуст — так не бывает, проверьте доступ к репозиторию' }

$mergedRaw = & gh pr list --repo $Repo --state merged --limit 200 --json headRefName --jq '.[].headRefName'
if ($LASTEXITCODE -ne 0) { Fail 'не удалось получить список смерженных PR' }

$openRaw = & gh pr list --repo $Repo --state open --limit 200 --json headRefName --jq '.[].headRefName'
if ($LASTEXITCODE -ne 0) { Fail 'не удалось получить список открытых PR' }

$merged = @($mergedRaw) | Where-Object { $_ }
$open = @($openRaw) | Where-Object { $_ }

Write-Host "Веток на GitHub: $($existing.Count); смерженных PR: $($merged.Count); открытых PR: $($open.Count)"

if ($merged.Count -eq 0) {
    Write-Host 'Смерженных PR не найдено — удалять нечего.'
    exit 0
}

# --- Отбор ------------------------------------------------------------------
$toDelete = @()
$skipped = @()

foreach ($branch in ($merged | Sort-Object -Unique)) {
    if (-not $existing.ContainsKey($branch)) { continue }  # уже удалена, это норма
    if ($branch -eq $defaultBranch) { $skipped += "$branch (ветка по умолчанию)"; continue }
    if ($existing[$branch])         { $skipped += "$branch (защищена правилами)"; continue }
    if ($open -contains $branch)    { $skipped += "$branch (есть открытый PR)"; continue }
    $toDelete += $branch
}

foreach ($s in $skipped) { Write-Host "  пропуск: $s" -ForegroundColor Yellow }

if ($toDelete.Count -eq 0) {
    Write-Host 'Веток смерженных PR на GitHub не осталось — чисто.' -ForegroundColor Green
    exit 0
}

Write-Host ''
Write-Host "К удалению ($($toDelete.Count)):"
foreach ($b in $toDelete) { Write-Host "  $b" }

# --- Удаление ---------------------------------------------------------------
if (-not $Apply) {
    Write-Host ''
    Write-Host 'Это предварительный просмотр. Ничего не удалено.' -ForegroundColor Cyan
    Write-Host 'Для удаления запустите тот же скрипт с ключом -Apply'
    exit 0
}

Write-Host ''
$failed = 0
foreach ($b in $toDelete) {
    & gh api -X DELETE "repos/$Repo/git/refs/heads/$b" *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  удалена: $b" -ForegroundColor Green
    } else {
        Write-Host "  НЕ УДАЛЕНА: $b" -ForegroundColor Red
        $failed++
    }
}

if ($failed -gt 0) {
    Fail "не удалось удалить веток: $failed из $($toDelete.Count)"
}

Write-Host ''
Write-Host "Удалено веток: $($toDelete.Count)" -ForegroundColor Green
