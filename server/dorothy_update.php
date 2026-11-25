<?php
/**
 * dorothy_update.php — 도로시 "비공개 레포" 자동 업데이트 중계기
 *
 * 목적: GitHub를 비공개(private)로 바꾸면 앱이 직접 릴리스를 못 받는다.
 *      이 파일이 회사 서버에서 GitHub에 대신 물어보고, 앱에는 "지금만 쓸 수 있는"
 *      다운로드 주소만 넘겨준다. → GitHub 열쇠(토큰)는 이 서버에만 있고, 폰(APK)엔 없다.
 *
 * 보호: 같은 폴더의 dorothy_report.php 와 똑같은 X-Dorothy-Token 헤더로 보호된다.
 *
 * ▶ 올리는 곳: dorothy_report.php 가 있는 바로 그 폴더에 이 파일을 그대로 올리세요.
 * ▶ 채울 값: 아래 "설정" 3줄만 채우면 끝.
 */

// ===== 설정: 이 3줄만 채우세요 =========================================
$DOROTHY_TOKEN = 'PUT_YOUR_DOROTHY_TOKEN';   // 도로시 앱 설정의 '회사 토큰'과 똑같은 값
$GITHUB_TOKEN  = 'PUT_YOUR_GITHUB_TOKEN';    // GitHub 토큰(아래 안내: Contents=Read, dorothy-brew 1개만)
$REPO          = 'siwoo2223/dorothy-brew';   // 레포 (그대로 두세요)
// =====================================================================

header('Content-Type: application/json; charset=utf-8');

// 1) 도로시 토큰 확인 (아무나 못 부르게)
$hdr = isset($_SERVER['HTTP_X_DOROTHY_TOKEN']) ? $_SERVER['HTTP_X_DOROTHY_TOKEN'] : '';
if (!is_string($hdr) || !hash_equals($DOROTHY_TOKEN, $hdr)) {
    http_response_code(403);
    echo json_encode(['error' => 'forbidden']);
    exit;
}

/** GitHub API 호출 헬퍼. $followLocation=false면 헤더까지 받아 Location을 읽을 수 있다. */
function gh_call($url, $token, $accept, $followLocation) {
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HTTPHEADER => [
            'Authorization: Bearer ' . $token,
            'Accept: ' . $accept,
            'User-Agent: dorothy-updater',
            'X-GitHub-Api-Version: 2022-11-28',
        ],
        CURLOPT_FOLLOWLOCATION => $followLocation,
        CURLOPT_HEADER => $followLocation ? false : true,
        CURLOPT_TIMEOUT => 30,
        CURLOPT_CONNECTTIMEOUT => 15,
    ]);
    $resp  = curl_exec($ch);
    $code  = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $hsize = curl_getinfo($ch, CURLINFO_HEADER_SIZE);
    $err   = curl_error($ch);
    curl_close($ch);
    return [$code, $resp, $hsize, $err];
}

$action = isset($_GET['action']) ? $_GET['action'] : 'check';

// 2) 최신 릴리스 정보
list($code, $body, , $err) = gh_call("https://api.github.com/repos/$REPO/releases/latest", $GITHUB_TOKEN, 'application/vnd.github+json', true);
if ($code !== 200) {
    http_response_code(502);
    echo json_encode(['error' => 'github', 'status' => $code, 'detail' => $err]);
    exit;
}
$rel   = json_decode($body, true);
$tag   = isset($rel['tag_name']) ? $rel['tag_name'] : '';
$build = 0;
if (preg_match('/build-(\d+)/', $tag, $m)) { $build = intval($m[1]); }

// apk 에셋의 API 주소 찾기
$assetUrl = '';
if (!empty($rel['assets']) && is_array($rel['assets'])) {
    foreach ($rel['assets'] as $a) {
        if (isset($a['name']) && substr(strtolower($a['name']), -4) === '.apk') {
            $assetUrl = isset($a['url']) ? $a['url'] : '';
            break;
        }
    }
}

// 3-A) 버전 확인
if ($action === 'check') {
    echo json_encode([
        'build' => $build,
        'tag'   => $tag,
        'name'  => isset($rel['name']) ? $rel['name'] : $tag,
        'notes' => isset($rel['body']) ? $rel['body'] : '',
    ], JSON_UNESCAPED_UNICODE);
    exit;
}

// 3-B) 다운로드 주소(짧게 유효한 서명 URL) 받아 넘기기
if ($action === 'download') {
    if (!$assetUrl) {
        http_response_code(404);
        echo json_encode(['error' => 'no_apk']);
        exit;
    }
    // 에셋 API에 octet-stream으로 요청하면 GitHub가 서명된 S3 주소로 302 리다이렉트한다.
    // 그 Location(서명 URL)을 앱에 넘기면, 앱은 토큰 없이 그 주소에서 바로 받는다.
    list($code2, $resp2, $hsize2, $err2) = gh_call($assetUrl, $GITHUB_TOKEN, 'application/octet-stream', false);
    $headers = substr($resp2, 0, $hsize2);
    $loc = '';
    if (preg_match('/^location:\s*(.+)\r?$/mi', $headers, $mm)) { $loc = trim($mm[1]); }
    if (!$loc) {
        http_response_code(502);
        echo json_encode(['error' => 'no_location', 'status' => $code2, 'detail' => $err2]);
        exit;
    }
    echo json_encode(['url' => $loc], JSON_UNESCAPED_UNICODE);
    exit;
}

http_response_code(400);
echo json_encode(['error' => 'bad_action']);
