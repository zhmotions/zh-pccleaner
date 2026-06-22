<?php
/** Force-download ZH PC Cleaner (.exe) with correct headers. Picks the newest matching file. */
function pick(): string {
    $hits = glob(__DIR__ . '/ZH-PC-Cleaner*.exe') ?: (glob(__DIR__ . '/ZH PC Cleaner*.exe') ?: []);
    if (!$hits) return '';
    usort($hits, fn($a, $b) => filemtime($b) <=> filemtime($a));
    return $hits[0];
}
$file = pick();
if ($file === '' || !is_file($file)) { http_response_code(404); echo 'Not found'; exit; }
while (ob_get_level() > 0) { ob_end_clean(); }
@set_time_limit(0);
header('Content-Type: application/octet-stream');
header('Content-Disposition: attachment; filename="' . basename($file) . '"');
header('Content-Length: ' . filesize($file));
header('X-Content-Type-Options: nosniff');
$fp = fopen($file, 'rb');
if ($fp === false) { http_response_code(500); exit; }
while (!feof($fp)) { echo fread($fp, 1024 * 256); flush(); }
fclose($fp); exit;
