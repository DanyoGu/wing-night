param(
    [ValidateSet('Verify','Heartbeat','Report','Cleanup','Retire')]
    [string]$Action = 'Verify'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Disable-AzContextAutosave -Scope Process | Out-Null
Connect-AzAccount -Identity -Subscription '26e7b9c0-b9a9-4745-9150-2a87fdb9749e' | Out-Null
$root = '/subscriptions/26e7b9c0-b9a9-4745-9150-2a87fdb9749e/resourceGroups'
$vm = "$root/rg-hotones-event/providers/Microsoft.Compute/virtualMachines/vm-hotones"
$storage = 'https://sthotones20261106.blob.core.windows.net'
$queue = 'https://sthotones20261106.queue.core.windows.net/email-delivery/messages'
$senderEndpoint = 'https://acs-hotones.unitedstates.communication.azure.com'
$sender = 'DoNotReply@b364dbde-247f-4d8c-b92d-e50898d2f8d8.azurecomm.net'
$recipient = 'alvinli222@gmail.com'
$reportAt = [datetime]'2026-11-09T04:00:00Z'
$deleteAt = [datetime]'2026-11-09T13:00:00Z'
$endAt = [datetime]'2026-11-10T13:00:00Z'
$script:lease = $null

function Token($resource) {
    $value = (Get-AzAccessToken -ResourceUrl $resource).Token
    if ($value -is [securestring]) {
        return [System.Net.NetworkCredential]::new('', $value).Password
    }
    return $value
}

function Http($method, $url, $audience, $body = $null, $extra = @{}) {
    $headers = @{Authorization = "Bearer $(Token $audience)"}
    foreach ($key in $extra.Keys) { $headers[$key] = $extra[$key] }
    $params = @{Method=$method; Uri=$url; Headers=$headers; UseBasicParsing=$true; TimeoutSec=45}
    if ($null -ne $body) { $params.Body = $body }
    Invoke-WebRequest @params
}

function JsonBody($value) {
    return ,([System.Text.Encoding]::UTF8.GetBytes(($value | ConvertTo-Json -Depth 30 -Compress)))
}

function Arm($method, $path, $body = $null) {
    Http $method "https://management.azure.com$path" 'https://management.azure.com/' $body @{'Content-Type'='application/json'}
}

function Blob($name) {
    $response = Http GET "$storage/reports/$name" 'https://storage.azure.com/' $null @{'x-ms-version'='2023-11-03'}
    return ,$response.RawContentStream.ToArray()
}

function ReadJson($name) {
    [System.Text.Encoding]::UTF8.GetString((Blob $name)) | ConvertFrom-Json
}

function Put($name, $bytes, $extra = @{}) {
    $headers = @{'x-ms-version'='2023-11-03'; 'x-ms-blob-type'='BlockBlob'; 'Content-Type'='application/json'}
    foreach ($key in $extra.Keys) { $headers[$key] = $extra[$key] }
    Http PUT "$storage/reports/$name" 'https://storage.azure.com/' $bytes $headers | Out-Null
}

function RenewLock {
    if ($script:lease) {
        Http PUT "$storage/reports/lock?comp=lease" 'https://storage.azure.com/' $null @{
            'x-ms-version'='2023-11-03'; 'x-ms-lease-action'='renew'; 'x-ms-lease-id'=$script:lease
        } | Out-Null
    }
}

function WaitArm($response) {
    if ([int]$response.StatusCode -ne 202) { return ($response.Content | ConvertFrom-Json) }
    $location = $response.Headers['Location']
    $async = $response.Headers['Azure-AsyncOperation']
    $poll = $async
    if (-not $poll) { $poll = $location }
    if (-not $poll) { throw 'ARM accepted an operation without a polling endpoint' }
    for ($i=0; $i -lt 40; $i++) {
        RenewLock
        Start-Sleep -Seconds 10
        $result = (Http GET $poll 'https://management.azure.com/').Content | ConvertFrom-Json
        if ($result.status -in @('Failed','Canceled')) { throw "ARM operation failed: $($result | ConvertTo-Json -Depth 10 -Compress)" }
        if ($result.status -eq 'Succeeded' -or $result.value) {
            if ($location -and $async) {
                return ((Http GET $location 'https://management.azure.com/').Content | ConvertFrom-Json)
            }
            return $result
        }
    }
    throw 'ARM operation exceeded bounded wait'
}

function VMCommand($command) {
    $result = WaitArm (Arm POST "$vm/runCommand?api-version=2024-03-01" (JsonBody @{
        commandId='RunShellScript'; script=@("set -eu", $command)
    }))
    $output = @($result.value | ForEach-Object { $_.message }) -join "`n"
    $parts = $output -split '\[stderr\]', 2
    $stdout = $parts[0]
    $stderr = if ($parts.Length -gt 1) { $parts[1].Trim() } else { '' }
    if ($stderr) { throw "VM export command reported errors: $stderr" }
    $json = @($stdout -split "`n" | Where-Object { $_.StartsWith('{') })[-1]
    if (-not $json) { throw "VM command returned no verified manifest: $stdout" }
    $json | ConvertFrom-Json
}

function Hash($bytes) {
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($algorithm.ComputeHash($bytes))).Replace('-','').ToLowerInvariant() }
    finally { $algorithm.Dispose() }
}

function Export($stop = $false) {
    $command = ''
    if ($stop) { $command = 'systemctl stop wing-night.service; ' }
    $manifest = VMCommand ($command + 'set -a; . /etc/wing-night/environment; set +a; python3 /opt/wing-night/deploy/operations.py export')
    $remote = ReadJson "$($manifest.prefix)/manifest.json"
    if ($remote.integrity -ne 'ok' -or $remote.sha256 -ne $manifest.sha256) { throw 'Export manifest integrity mismatch' }
    $csv = Blob "$($manifest.prefix)/report.csv"
    $html = Blob "$($manifest.prefix)/report.html"
    if ((Hash $csv) -ne $manifest.csv_sha256 -or (Hash $html) -ne $manifest.html_sha256) { throw 'Export checksum mismatch' }
    return [pscustomobject]@{manifest=$manifest; csv=$csv; html=$html}
}

function SaveState {
    RenewLock
    Put 'lifecycle.json' (JsonBody $script:state)
}

function WaitDelivery($record) {
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        RenewLock
        $operation = (Http GET "$senderEndpoint/emails/operations/$($record.operation)?api-version=2023-03-31" 'https://communication.azure.com/').Content | ConvertFrom-Json
        if ($operation.status -in @('Failed','Canceled')) { throw "Email send failed: $($operation | ConvertTo-Json -Depth 10 -Compress)" }
        $response = Http GET "$queue`?numofmessages=32&visibilitytimeout=30" 'https://storage.azure.com/' $null @{'x-ms-version'='2023-11-03'}
        [xml]$messages = $response.Content
        foreach ($message in @($messages.QueueMessagesList.QueueMessage)) {
            if (-not $message) { continue }
            $text = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($message.MessageText))
            foreach ($event in @($text | ConvertFrom-Json)) {
                if ($event.eventType -eq 'Microsoft.Communication.EmailDeliveryReportReceived' -and
                    $event.data.messageId -eq $record.operation -and $event.data.recipient -eq $recipient) {
                    Put "delivery/$($record.operation).json" (JsonBody $event)
                    $record.delivery = $event.data.status
                    $record.delivery_utc = $event.eventTime
                    SaveState
                }
            }
            Http DELETE "$queue/$($message.MessageId)?popreceipt=$([uri]::EscapeDataString($message.PopReceipt))" 'https://storage.azure.com/' $null @{'x-ms-version'='2023-11-03'} | Out-Null
        }
        if ($record.delivery -eq 'Delivered' -and $operation.status -eq 'Succeeded') {
            Write-Output "Provider confirms recipient mail server accepted report $($record.operation); inbox/read status is not known."
            return
        }
        if ($record.delivery -and $record.delivery -ne 'Delivered') { throw "Email delivery failed: $($record.delivery)" }
        Start-Sleep -Seconds 20
    }
    throw 'No successful provider delivery confirmation within bounded wait; data is preserved'
}

function SendReport($slot, $export, $subject) {
    $record = $script:state.$slot
    if (-not $record) {
        $record = [pscustomobject]@{
            operation=[guid]::NewGuid().ToString(); prefix=$export.manifest.prefix;
            results_sha256=$export.manifest.results_sha256; delivery=$null; delivery_utc=$null;
            submitted=$false; snapshot_utc=$export.manifest.snapshot_utc
        }
        $script:state.$slot = $record
        SaveState
    }
    if ($record.delivery -eq 'Delivered') { return }
    if (-not $record.submitted) {
        try {
            Http GET "$senderEndpoint/emails/operations/$($record.operation)?api-version=2023-03-31" 'https://communication.azure.com/' | Out-Null
            $record.submitted = $true
            SaveState
        } catch {
            if ([int]$_.Exception.Response.StatusCode -ne 404) { throw }
        }
    }
    if (-not $record.submitted) {
        # Persist the id before sending; retries use the same provider operation id.
        $csv = Blob "$($record.prefix)/report.csv"
        $html = Blob "$($record.prefix)/report.html"
        $body = JsonBody @{
            senderAddress=$sender; recipients=@{to=@(@{address=$recipient})};
            content=@{subject=$subject; html=[System.Text.Encoding]::UTF8.GetString($html);
                plainText="Wing Night recorded results snapshot: $($record.snapshot_utc). See the attached CSV. Later votes are not included; unfinished rooms are not final results."};
            attachments=@(@{name='wing-night-results.csv'; contentType='text/csv'; contentInBase64=[Convert]::ToBase64String($csv)});
            userEngagementTrackingDisabled=$true
        }
        if ($body.Length -gt 9000000) { throw 'Report exceeds safe email size limit' }
        RenewLock
        $accepted = Http POST "$senderEndpoint/emails:send?api-version=2023-03-31" 'https://communication.azure.com/' $body @{
            'Content-Type'='application/json'; 'Operation-Id'=$record.operation
        }
        $sendResult = $accepted.Content | ConvertFrom-Json
        if ([int]$accepted.StatusCode -ne 202 -or $sendResult.id -ne $record.operation) {
            throw 'Email provider acceptance did not match the persisted operation id'
        }
        $record.submitted = $true
        SaveState
    }
    WaitDelivery $record
}

function CheckOwned($group, $names) {
    $types = @{
        'vm-hotones'='Microsoft.Compute/virtualMachines';
        'disk-hotones-os'='Microsoft.Compute/disks'; 'disk-hotones-data'='Microsoft.Compute/disks';
        'pip-hotones'='Microsoft.Network/publicIPAddresses'; 'vnet-hotones'='Microsoft.Network/virtualNetworks';
        'nic-hotones'='Microsoft.Network/networkInterfaces'; 'nsg-hotones'='Microsoft.Network/networkSecurityGroups';
        'sthotones20261106'='Microsoft.Storage/storageAccounts'; 'aa-hotones'='Microsoft.Automation/automationAccounts';
        'aa-hotones/hotones-lifecycle'='Microsoft.Automation/automationAccounts/runbooks';
        'id-hotones-deploy'='Microsoft.ManagedIdentity/userAssignedIdentities';
        'acs-hotones'='Microsoft.Communication/CommunicationServices'; 'email-hotones'='Microsoft.Communication/EmailServices';
        'email-hotones/AzureManagedDomain'='Microsoft.Communication/EmailServices/Domains';
        'eg-hotones-email'='Microsoft.EventGrid/systemTopics'; 'ag-hotones'='Microsoft.Insights/actionGroups';
        'alert-hotones-jobs'='Microsoft.Insights/metricAlerts'
    }
    $metadata = (Arm GET "$root/$group`?api-version=2022-09-01").Content | ConvertFrom-Json
    if ($metadata.tags.managedBy -ne 'wing-night-deployment' -or $metadata.tags.event -ne '2026-11-08') {
        throw "Refusing unowned group $group"
    }
    $resources = (Arm GET "$root/$group/resources?api-version=2021-04-01").Content | ConvertFrom-Json
    if ($resources.nextLink) { throw 'Unexpected resource inventory pagination' }
    foreach ($resource in $resources.value) {
        if ($resource.name -notin $names -or $resource.type -ne $types[$resource.name]) {
            throw "Refusing unexpected resource $($resource.id)"
        }
        if ($resource.tags.managedBy -and $resource.tags.managedBy -ne 'wing-night-deployment') {
            throw "Refusing resource with conflicting ownership: $($resource.id)"
        }
    }
}

function Exists($group) {
    try { Arm GET "$root/$group`?api-version=2022-09-01" | Out-Null; return $true }
    catch { if ([int]$_.Exception.Response.StatusCode -eq 404) { return $false }; throw }
}

if ($Action -eq 'Heartbeat') {
    if ([datetime]::UtcNow -ge $deleteAt) { Write-Output 'Event runtime window ended'; return }
    $response = Http GET "$storage/backups/latest.json" 'https://storage.azure.com/' $null @{'x-ms-version'='2023-11-03'}
    $latest = $response.Content | ConvertFrom-Json
    if ($latest.integrity -ne 'ok' -or ([datetime]::UtcNow - [datetime]$latest.snapshot_utc).TotalMinutes -gt 5) {
        throw 'Off-VM backup heartbeat is stale or invalid'
    }
    Write-Output "Backup healthy: $($latest.snapshot_utc)"
    return
}
if ($Action -eq 'Report' -and ([datetime]::UtcNow -lt $reportAt -or [datetime]::UtcNow -ge $deleteAt)) { throw 'Outside authorized report window' }
if ($Action -in @('Cleanup','Retire') -and [datetime]::UtcNow -lt $deleteAt) { throw 'Cleanup cannot run before Monday 08:00 EST' }
if ([datetime]::UtcNow -gt $endAt) { throw 'Automatic retry window expired; manual recovery required' }

if ($Action -eq 'Retire' -and -not (Exists 'rg-hotones-backups')) {
    $metadata = (Arm GET "$root/rg-hotones-lifecycle`?api-version=2022-09-01").Content | ConvertFrom-Json
    if ((Exists 'rg-hotones-event') -or $metadata.tags.cleanupVerified -ne 'export-email-event-deletion-confirmed') {
        throw 'Missing backups without independent retirement marker; manual recovery required'
    }
    CheckOwned 'rg-hotones-lifecycle' @('aa-hotones','aa-hotones/hotones-lifecycle','id-hotones-deploy','acs-hotones','email-hotones','email-hotones/AzureManagedDomain','eg-hotones-email','ag-hotones','alert-hotones-jobs')
    Arm DELETE "$root/rg-hotones-lifecycle`?api-version=2022-09-01" | Out-Null
    Write-Output 'Resumed verified lifecycle retirement after backup deletion'
    return
}

try {
    try { Put 'lock' ([byte[]]@()) @{'If-None-Match'='*'} }
    catch { if ([int]$_.Exception.Response.StatusCode -notin @(409,412)) { throw } }
    $script:lease = [guid]::NewGuid().ToString()
    Http PUT "$storage/reports/lock?comp=lease" 'https://storage.azure.com/' $null @{
        'x-ms-version'='2023-11-03'; 'x-ms-lease-action'='acquire';
        'x-ms-lease-duration'='60'; 'x-ms-proposed-lease-id'=$script:lease
    } | Out-Null
    try { $script:state = ReadJson 'lifecycle.json' }
    catch {
        if ([int]$_.Exception.Response.StatusCode -ne 404) { throw }
        $script:state = [pscustomobject]@{verified=$null; sunday=$null; final=$null; cleanup='pending'}
    }
    if ($Action -eq 'Verify') {
        CheckOwned 'rg-hotones-event' @('vm-hotones','disk-hotones-os','disk-hotones-data','pip-hotones','vnet-hotones','nic-hotones','nsg-hotones')
        CheckOwned 'rg-hotones-backups' @('sthotones20261106')
        CheckOwned 'rg-hotones-lifecycle' @('aa-hotones','aa-hotones/hotones-lifecycle','id-hotones-deploy','acs-hotones','email-hotones','email-hotones/AzureManagedDomain','eg-hotones-email','ag-hotones','alert-hotones-jobs')
        $export = Export
        # An intentionally invalid request verifies authentication without sending email.
        try {
            Http POST "$senderEndpoint/emails:send?api-version=2023-03-31" 'https://communication.azure.com/' (JsonBody @{}) @{'Content-Type'='application/json'} | Out-Null
            throw 'Email endpoint unexpectedly accepted an empty message'
        } catch { if ([int]$_.Exception.Response.StatusCode -ne 400) { throw } }
        Http GET "$queue`?peekonly=true&numofmessages=1" 'https://storage.azure.com/' $null @{'x-ms-version'='2023-11-03'} | Out-Null
        $script:state.verified = [pscustomobject]@{utc=[datetime]::UtcNow.ToString('o'); prefix=$export.manifest.prefix; sender_auth='verified-no-send'; checksums='ok'}
        SaveState
        Write-Output "Reporting integration verified without sending email; export $($export.manifest.prefix)"
    } elseif ($Action -eq 'Report') {
        if (-not $script:state.verified) { throw 'Reporting integration has not been verified' }
        if ($script:state.sunday -and $script:state.sunday.delivery -eq 'Delivered') { Write-Output 'Sunday report already confirmed'; return }
        SendReport 'sunday' (Export) 'Wing Night results - Sunday 23:00 EST snapshot'
    } elseif ($Action -eq 'Cleanup') {
        if (-not $script:state.verified -or -not $script:state.sunday -or $script:state.sunday.delivery -ne 'Delivered') {
            throw 'Sunday export and recipient-mail-server acceptance are not verified; refusing deletion'
        }
        if (Exists 'rg-hotones-event') {
            CheckOwned 'rg-hotones-event' @('vm-hotones','disk-hotones-os','disk-hotones-data','pip-hotones','vnet-hotones','nic-hotones','nsg-hotones')
            if ($script:state.cleanup -eq 'event-deleted') { throw 'Event group reappeared after deletion; refusing unexpected recreation' }
            if ($script:state.cleanup -ne 'export-and-email-verified') {
                $export = Export $true
                if ($export.manifest.results_sha256 -ne $script:state.sunday.results_sha256) {
                    SendReport 'final' $export 'Wing Night final results - Monday shutdown snapshot'
                    if ($script:state.final.results_sha256 -ne $export.manifest.results_sha256) {
                        throw 'Latest shutdown results differ from the confirmed final email'
                    }
                }
                $script:state.cleanup = 'export-and-email-verified'
                SaveState
            }
            RenewLock
            Arm DELETE "$root/rg-hotones-event`?api-version=2022-09-01" | Out-Null
            for ($i=0; $i -lt 40 -and (Exists 'rg-hotones-event'); $i++) { RenewLock; Start-Sleep -Seconds 10 }
            if (Exists 'rg-hotones-event') { throw 'Event deletion still in progress; backups preserved for retry' }
        } elseif ($script:state.cleanup -notin @('export-and-email-verified','event-deleted')) {
            throw 'VM is absent without verified cleanup state; refusing backup deletion'
        }
        $script:state.cleanup = 'event-deleted'
        SaveState
        Write-Output 'Event group deleted after verified export and provider delivery; Retire removes backups and scheduler.'
    } elseif ($Action -eq 'Retire') {
        if ($script:state.cleanup -ne 'event-deleted' -or (Exists 'rg-hotones-event')) { throw 'Event cleanup not complete; preserving backups' }
        CheckOwned 'rg-hotones-backups' @('sthotones20261106')
        CheckOwned 'rg-hotones-lifecycle' @('aa-hotones','aa-hotones/hotones-lifecycle','id-hotones-deploy','acs-hotones','email-hotones','email-hotones/AzureManagedDomain','eg-hotones-email','ag-hotones','alert-hotones-jobs')
        $script:state.cleanup = 'retirement-verified'
        SaveState
        Arm PATCH "$root/rg-hotones-lifecycle`?api-version=2022-09-01" (JsonBody @{tags=@{
            workload='hotones-event'; managedBy='wing-night-deployment'; event='2026-11-08';
            cleanupVerified='export-email-event-deletion-confirmed'
        }}) | Out-Null
        Arm DELETE "$root/rg-hotones-backups`?api-version=2022-09-01" | Out-Null
        for ($i=0; $i -lt 40 -and (Exists 'rg-hotones-backups'); $i++) { Start-Sleep -Seconds 10 }
        if (Exists 'rg-hotones-backups') { throw 'Backup group deletion pending; lifecycle resources preserved' }
        Arm DELETE "$root/rg-hotones-lifecycle`?api-version=2022-09-01" | Out-Null
        Write-Output 'Owned backup and lifecycle groups deletion requested; no retained event archive.'
    }
} catch {
    $failure = $_
    if ($Action -eq 'Cleanup' -and (Exists 'rg-hotones-event')) {
        try { Arm POST "$vm/deallocate?api-version=2024-03-01" (JsonBody @{}) | Out-Null }
        catch { Write-Warning "Emergency deallocation also failed: $($_.Exception.Message)" }
        Write-Warning 'Destructive cleanup blocked. VM deallocation requested to halt compute charges; disks and backups retained for recovery.'
    }
    throw $failure
} finally {
    if ($script:lease) {
        try {
            Http PUT "$storage/reports/lock?comp=lease" 'https://storage.azure.com/' $null @{
                'x-ms-version'='2023-11-03'; 'x-ms-lease-action'='release'; 'x-ms-lease-id'=$script:lease
            } | Out-Null
        } catch { Write-Warning "Lease release failed or backing storage was intentionally deleted: $($_.Exception.Message)" }
    }
}
