<#
.SYNOPSIS
    Калибровка cleanup-merged-branches.ps1.

.DESCRIPTION
    Гоняет НАСТОЯЩИЙ скрипт на подставных данных: в области видимости теста
    объявляется функция gh, и вызовы вида "& gh ..." попадают в неё вместо
    настоящего GitHub CLI. Ни один запрос наружу не уходит, ни одна ветка
    не удаляется.

    Проверяется не то, что скрипт работает на счастливом пути — это видно и
    так. Проверяется, что срабатывает КАЖДЫЙ предохранитель по отдельности:
    удаление ветки по умолчанию, защищённой, с открытым PR, а также громкий
    отказ при сбое запроса и при пустом списке веток.

.EXAMPLE
    .\cleanup-merged-branches.Tests.ps1
#>

$ErrorActionPreference = 'Stop'
$script:Target = Join-Path $PSScriptRoot 'cleanup-merged-branches.ps1'
if (-not (Test-Path $script:Target)) { throw "Не найден проверяемый скрипт: $($script:Target)" }

# Состояние текущей клетки: чем отвечает заглушка и что она записала.
$global:Fixture = $null
$global:Deleted = @()

function gh {
    $args_ = $args -join ' '

    if ($args_ -like 'auth status*') { $global:LASTEXITCODE = 0; return }

    if ($args_ -like '*-X DELETE*') {
        $ref = ($args | Where-Object { $_ -like 'repos/*/git/refs/heads/*' })
        $global:Deleted += ($ref -replace '.*/git/refs/heads/', '')
        $global:LASTEXITCODE = $global:Fixture.DeleteCode
        return
    }
    if ($args_ -like '*--jq .default_branch*') {
        $global:LASTEXITCODE = 0; return $global:Fixture.Default
    }
    if ($args_ -like '*branches*--paginate*') {
        $global:LASTEXITCODE = $global:Fixture.BranchesCode
        if ($global:Fixture.BranchesCode -ne 0) { return }
        return $global:Fixture.Branches
    }
    if ($args_ -like 'pr list*merged*') { $global:LASTEXITCODE = 0; return $global:Fixture.Merged }
    if ($args_ -like 'pr list*open*')   { $global:LASTEXITCODE = 0; return $global:Fixture.Open }

    throw "Заглушка gh не знает такого вызова: $args_"
}

function New-Fixture {
    param(
        [string[]]$Branches = @("main`tfalse", "feature-a`tfalse"),
        [string[]]$Merged = @('feature-a'),
        [string[]]$Open = @(),
        [string]$Default = 'main',
        [int]$BranchesCode = 0,
        [int]$DeleteCode = 0
    )
    @{ Branches = $Branches; Merged = $Merged; Open = $Open
       Default = $Default; BranchesCode = $BranchesCode; DeleteCode = $DeleteCode }
}

$script:Pass = 0
$script:Fail = 0

function Test-Case {
    param([string]$Name, [hashtable]$Fixture, [switch]$Apply,
          [string[]]$ExpectDeleted = @(), [int]$ExpectExit = 0, [string]$ExpectText)

    $global:Fixture = $Fixture
    $global:Deleted = @()
    $global:LASTEXITCODE = 0

    # Write-Host пишет в информационный поток: 2>&1 его НЕ ловит, нужен *>&1
    $out = & $script:Target -Repo 'test/repo' -Apply:$Apply *>&1 | Out-String
    $code = $LASTEXITCODE

    $problems = @()
    if ($code -ne $ExpectExit) { $problems += "код возврата $code, ожидался $ExpectExit" }

    $got = @($global:Deleted | Sort-Object)
    $want = @($ExpectDeleted | Sort-Object)
    if (($got -join ',') -ne ($want -join ',')) {
        $problems += "удалено [$($got -join ', ')], ожидалось [$($want -join ', ')]"
    }
    if ($ExpectText -and $out -notmatch [regex]::Escape($ExpectText)) {
        $problems += "в выводе нет фрагмента '$ExpectText'"
    }

    if ($problems.Count -eq 0) {
        Write-Host "  ok   $Name" -ForegroundColor Green
        $script:Pass++
    } else {
        Write-Host "  ПРОВАЛ $Name" -ForegroundColor Red
        foreach ($p in $problems) { Write-Host "         $p" -ForegroundColor Red }
        $script:Fail++
    }
}

Write-Host 'Калибровка cleanup-merged-branches.ps1'
Write-Host ''

# --- Счастливый путь --------------------------------------------------------
Test-Case 'без -Apply не удаляет ничего' `
    (New-Fixture) -ExpectDeleted @() -ExpectText 'Ничего не удалено'

Test-Case 'с -Apply удаляет ветку смерженного PR' `
    (New-Fixture) -Apply -ExpectDeleted @('feature-a')

# --- Предохранители: каждый проверяется отдельно -----------------------------
Test-Case 'ветку по умолчанию не удаляет, даже если она в списке смерженных' `
    (New-Fixture -Merged @('main', 'feature-a')) -Apply `
    -ExpectDeleted @('feature-a') -ExpectText 'ветка по умолчанию'

Test-Case 'защищённую ветку не удаляет' `
    (New-Fixture -Branches @("main`tfalse", "release`ttrue") -Merged @('release')) -Apply `
    -ExpectDeleted @() -ExpectText 'защищена правилами'

Test-Case 'ветку с открытым PR не удаляет' `
    (New-Fixture -Merged @('feature-a') -Open @('feature-a')) -Apply `
    -ExpectDeleted @() -ExpectText 'есть открытый PR'

Test-Case 'уже удалённую ветку не пытается удалять повторно' `
    (New-Fixture -Branches @("main`tfalse") -Merged @('feature-a')) -Apply `
    -ExpectDeleted @() -ExpectText 'не осталось'

# --- Громкие отказы вместо тихого успеха -------------------------------------
Test-Case 'сбой запроса веток — отказ, а не пустой успех' `
    (New-Fixture -BranchesCode 1) -ExpectExit 1 -ExpectText 'ОТКАЗ'

Test-Case 'пустой список веток — отказ, а не «чисто»' `
    (New-Fixture -Branches @()) -ExpectExit 1 -ExpectText 'ОТКАЗ'

Test-Case 'нет смерженных PR — спокойный выход без удалений' `
    (New-Fixture -Merged @()) -Apply -ExpectDeleted @() -ExpectText 'удалять нечего'

Test-Case 'провал удаления на стороне GitHub — отказ с ненулевым кодом' `
    (New-Fixture -DeleteCode 1) -Apply `
    -ExpectDeleted @('feature-a') -ExpectExit 1 -ExpectText 'НЕ УДАЛЕНА'

Write-Host ''
Write-Host "Пройдено: $script:Pass, провалено: $script:Fail"
if ($script:Fail -gt 0) { exit 1 }
Write-Host 'Калибровка чистая.' -ForegroundColor Green
# Явный ноль обязателен: иначе наружу утекает код последней клетки,
# и чистая калибровка рапортует провал.
exit 0
