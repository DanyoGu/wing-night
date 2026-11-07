$ErrorActionPreference = 'Stop'
$source = Get-Content (Join-Path $PSScriptRoot '..\deploy\lifecycle.ps1') -Raw
$source = $source.Replace('[datetime]::UtcNow', '$script:clock')
$marker = "if (`$Action -eq 'Heartbeat') {"
$offset = $source.IndexOf($marker)
if ($offset -lt 0) { throw 'Runbook test injection point missing' }
$mocks = @'
function Put($name, $bytes, $extra) {}
function ReadJson($name) { return $script:testState }
function RenewLock {}
function SaveState {}
function Token($resource) { return 'not-a-real-token' }
function Http($method, $url, $audience, $body, $extra) { return [pscustomobject]@{Content='{}';StatusCode=200} }
function Exists($group) { return $script:existing[$group] }
function CheckOwned($group, $names) { if ($script:unexpected) { throw 'Unexpected resource' } }
function Export($stop) {
    $script:calls.Add('export')
    return [pscustomobject]@{manifest=[pscustomobject]@{results_sha256=$script:results;prefix='verified-export';snapshot_utc='now'}}
}
function SendReport($slot, $export, $subject) {
    $script:calls.Add('send-final')
    if ($script:sendFailure) { throw 'Simulated bounce' }
    $script:state.$slot = [pscustomobject]@{delivery='Delivered';results_sha256=$script:results}
}
function Arm($method, $path, $body) {
    if ($method -eq 'DELETE') {
        $script:calls.Add("delete:$path")
        if ($path -like '*rg-hotones-event?*') { $script:existing['rg-hotones-event']=$false }
        if ($path -like '*rg-hotones-backups?*') { $script:existing['rg-hotones-backups']=$false }
    }
    if ($method -eq 'POST' -and $path -like '*deallocate*') { $script:calls.Add('deallocate') }
    return [pscustomobject]@{Content='{"tags":{"managedBy":"wing-night-deployment","event":"2026-11-08"}}';StatusCode=200}
}
'@
$source = $source.Insert($offset, $mocks + "`n")
function Disable-AzContextAutosave {}
function Connect-AzAccount {}

function RunCase($action, $time, $delivery, $results, $sendFailure=$false, $unexpected=$false) {
    $script:clock = [datetime]$time
    $script:calls = [System.Collections.Generic.List[string]]::new()
    $script:existing = @{'rg-hotones-event'=$true; 'rg-hotones-backups'=$true; 'rg-hotones-lifecycle'=$true}
    $script:testState = [pscustomobject]@{
        verified=[pscustomobject]@{checksums='ok'};
        sunday=if ($delivery) {[pscustomobject]@{delivery=$delivery;results_sha256='old'}} else {$null};
        final=$null; cleanup='pending'
    }
    $script:results=$results
    $script:sendFailure=$sendFailure
    $script:unexpected=$unexpected
    $script:failure=$null
    try { & ([scriptblock]::Create($source)) -Action $action | Out-Null }
    catch { $script:failure=$_.Exception.Message }
    return @($script:calls)
}
function Assert($value, $message) { if (-not $value) { throw $message } }

$calls=RunCase Cleanup '2026-11-09T12:59:59Z' Delivered old
Assert ($script:failure -like '*cannot run before*' -and $calls.Count -eq 0) 'Early cleanup must perform no actions'
$calls=RunCase Cleanup '2026-11-09T13:00:00Z' '' old
Assert ($script:failure -like '*refusing deletion*' -and $calls -contains 'deallocate' -and -not ($calls -like 'delete:*')) 'Missing Sunday report must deallocate and preserve data'
$calls=RunCase Cleanup '2026-11-09T13:00:00Z' Succeeded old
Assert ($script:failure -and $calls -contains 'deallocate' -and -not ($calls -like 'delete:*')) 'Send acceptance alone must never authorize deletion'
$calls=RunCase Cleanup '2026-11-09T13:00:00Z' Delivered new $true
Assert ($script:failure -like '*bounce*' -and $calls -contains 'deallocate' -and -not ($calls -like 'delete:*')) 'Final report bounce must preserve disks and backups'
$calls=RunCase Cleanup '2026-11-09T13:00:00Z' Delivered new $false $true
Assert ($script:failure -like '*Unexpected*' -and -not ($calls -like 'delete:*')) 'Unexpected resources must block deletion'
$calls=RunCase Cleanup '2026-11-09T13:00:00Z' Delivered new
Assert (-not $script:failure) "Successful cleanup failed: $script:failure"
Assert ($calls[0] -eq 'export' -and $calls[1] -eq 'send-final' -and $calls[2] -like 'delete:*rg-hotones-event*') 'Export and confirmed final email must precede event deletion'
$calls=RunCase Cleanup '2026-11-09T13:00:00Z' Delivered old
Assert (-not $script:failure -and $calls -notcontains 'send-final' -and ($calls -like 'delete:*rg-hotones-event*')) 'Unchanged scores should reuse confirmed Sunday report'
Write-Output '7 guarded lifecycle scenarios passed; no Azure calls or emails performed.'

$original = Get-Content (Join-Path $PSScriptRoot '..\deploy\lifecycle.ps1') -Raw
$ast = [System.Management.Automation.Language.Parser]::ParseInput($original, [ref]$null, [ref]$null)
foreach ($functionName in @('JsonBody','WaitDelivery')) {
    $definition = $ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $functionName}, $true)
    . ([scriptblock]::Create($definition.Extent.Text))
}
$recipient = 'alvinli222@gmail.com'
$senderEndpoint = 'https://sender.invalid'
$queue = 'https://queue.invalid/messages'
function Start-Sleep {}
function RenewLock {}
function Put {}
function SaveState {}
function Http($method, $url, $audience, $body, $extra) {
    if ($url -like '*emails/operations/*') { return [pscustomobject]@{Content='{"status":"Succeeded"}'} }
    if ($method -eq 'DELETE') { return [pscustomobject]@{Content=''} }
    $event = @{eventType='Microsoft.Communication.EmailDeliveryReportReceived';eventTime='2026-11-09T04:01:00Z';
        data=@{messageId='test-operation';recipient=$recipient;status=$script:deliveryStatus}} | ConvertTo-Json -Depth 10 -Compress
    $encoded = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($event))
    return [pscustomobject]@{Content="<QueueMessagesList><QueueMessage><MessageId>test</MessageId><PopReceipt>receipt</PopReceipt><MessageText>$encoded</MessageText></QueueMessage></QueueMessagesList>"}
}
$record = [pscustomobject]@{operation='test-operation';delivery=$null;delivery_utc=$null}
$script:deliveryStatus='Delivered'
WaitDelivery $record | Out-Null
Assert ($record.delivery -eq 'Delivered') 'Actual delivery-event parser must confirm recipient MTA acceptance'
$record.delivery=$null
$script:deliveryStatus='Bounced'
$bounce=$false
try { WaitDelivery $record | Out-Null } catch { $bounce=$_.Exception.Message -like '*Bounced*' }
Assert $bounce 'Actual delivery-event parser must reject a bounce'
Write-Output '2 delivery-event parsing scenarios passed without contacting any service.'
