<?php
/**
 * Plugin Name: KF로지스틱 입고 알림
 * Description: 입고를 등록(사진·내용)해 두면, 사무실 PC 카카오톡 발송 프로그램이 정해진 시간(예: 매일 17시)에 고객별로 모아 카톡으로 보냅니다.
 * Version: 1.0.0
 * Author: KF로지스틱
 * Requires at least: 5.8
 * Requires PHP: 7.4
 * Text Domain: kf-inbound-notify
 */

if (!defined('ABSPATH')) {
    exit;
}

define('KF_INBOUND_VERSION', '1.0.0');
define('KF_INBOUND_DB_VERSION', '1');
define('KF_INBOUND_CAP', 'edit_posts');        // 입고 등록·고객 관리: 편집자 이상
define('KF_INBOUND_ADMIN_CAP', 'manage_options'); // 설정·연결 키: 관리자
define('KF_INBOUND_NS', 'kf-inbound/v1');

const KF_INBOUND_DEFAULT_TEMPLATE = "[KF로지스틱 입고 안내]\n#{고객명} 고객님, 안녕하세요.\n아래 화물이 입고되었습니다.\n\n#{입고목록}\n\n입고 사진 함께 보내드립니다.\n문의사항은 언제든 연락 주세요. 감사합니다.";
const KF_INBOUND_DEFAULT_LINE = "▶ #{입고일} #{내용}";

const KF_INBOUND_STATUS = array(
    'pending'   => '대기 (예약 시간에 발송)',
    'sending'   => '발송 중',
    'sent'      => '✅ 발송 완료',
    'failed'    => '❌ 실패',
    'cancelled' => '취소',
);

// ───────────────────────── 설치 ─────────────────────────
function kf_inbound_tables() {
    global $wpdb;
    return array(
        'customers' => $wpdb->prefix . 'kf_customers',
        'inbound'   => $wpdb->prefix . 'kf_inbound',
    );
}

function kf_inbound_install() {
    global $wpdb;
    $t = kf_inbound_tables();
    $charset = $wpdb->get_charset_collate();
    require_once ABSPATH . 'wp-admin/includes/upgrade.php';

    dbDelta("CREATE TABLE {$t['customers']} (
  id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  name varchar(191) NOT NULL,
  room varchar(191) NOT NULL DEFAULT '',
  tab varchar(10) NOT NULL DEFAULT 'chats',
  memo varchar(255) NOT NULL DEFAULT '',
  active tinyint(1) NOT NULL DEFAULT 1,
  PRIMARY KEY  (id),
  UNIQUE KEY name (name)
) $charset;");

    dbDelta("CREATE TABLE {$t['inbound']} (
  id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  customer_id bigint(20) unsigned NOT NULL,
  received_on date NOT NULL,
  items text NOT NULL,
  photo_ids text NOT NULL,
  status varchar(20) NOT NULL DEFAULT 'pending',
  note text NOT NULL,
  created_by bigint(20) unsigned NOT NULL DEFAULT 0,
  created_at datetime NOT NULL,
  claimed_at datetime NULL,
  sent_at datetime NULL,
  PRIMARY KEY  (id),
  KEY status (status),
  KEY customer_id (customer_id)
) $charset;");

    if (!get_option('kf_inbound_key')) {
        update_option('kf_inbound_key', wp_generate_password(40, false, false), false);
    }
    add_option('kf_inbound_template', KF_INBOUND_DEFAULT_TEMPLATE);
    add_option('kf_inbound_line', KF_INBOUND_DEFAULT_LINE);
    update_option('kf_inbound_db_version', KF_INBOUND_DB_VERSION);
}
register_activation_hook(__FILE__, 'kf_inbound_install');

add_action('plugins_loaded', function () {
    if (get_option('kf_inbound_db_version') !== KF_INBOUND_DB_VERSION) {
        kf_inbound_install();
    }
});

// ───────────────────────── 데이터 ─────────────────────────
function kf_inbound_now() {
    return current_time('mysql');
}

function kf_inbound_customers($only_active = false) {
    global $wpdb;
    $t = kf_inbound_tables();
    $where = $only_active ? 'WHERE active = 1' : '';
    return $wpdb->get_results("SELECT * FROM {$t['customers']} $where ORDER BY name ASC");
}

function kf_inbound_customer_by_name($name) {
    global $wpdb;
    $t = kf_inbound_tables();
    return $wpdb->get_row($wpdb->prepare("SELECT * FROM {$t['customers']} WHERE name = %s", $name));
}

function kf_inbound_save_customer($id, $name, $room, $tab, $memo = '') {
    global $wpdb;
    $t = kf_inbound_tables();
    $data = array(
        'name' => $name,
        'room' => $room,
        'tab'  => $tab === 'friends' ? 'friends' : 'chats',
        'memo' => $memo,
    );
    if ($id) {
        return $wpdb->update($t['customers'], $data, array('id' => $id)) !== false;
    }
    $existing = kf_inbound_customer_by_name($name);
    if ($existing) {
        return $wpdb->update($t['customers'], $data, array('id' => $existing->id)) !== false;
    }
    return (bool) $wpdb->insert($t['customers'], $data);
}

function kf_inbound_photo_ids($row) {
    return array_values(array_filter(array_map('intval', explode(',', (string) $row->photo_ids))));
}

function kf_inbound_photo_payload($ids) {
    $out = array();
    foreach ($ids as $id) {
        $file = get_attached_file($id);
        if (!$file || !file_exists($file)) {
            continue;
        }
        $out[] = array(
            'id'       => $id,
            'filename' => wp_basename($file),
            'url'      => rest_url(KF_INBOUND_NS . '/photo/' . $id),
        );
    }
    return $out;
}

function kf_inbound_entry_payload($row) {
    return array(
        'id'          => (int) $row->id,
        'customer'    => array(
            'id'   => (int) $row->customer_id,
            'name' => (string) $row->c_name,
            'room' => (string) $row->c_room,
            'tab'  => (string) $row->c_tab,
        ),
        'received_on' => (string) $row->received_on,
        'items'       => (string) $row->items,
        'photos'      => kf_inbound_photo_payload(kf_inbound_photo_ids($row)),
    );
}

function kf_inbound_select_sql($where) {
    $t = kf_inbound_tables();
    return "SELECT i.*, c.name AS c_name, c.room AS c_room, c.tab AS c_tab
            FROM {$t['inbound']} i LEFT JOIN {$t['customers']} c ON c.id = i.customer_id
            WHERE $where ORDER BY i.id ASC";
}

// ───────────────────────── PC 발송 프로그램이 쓰는 통로(REST) ─────────────────────────
function kf_inbound_check_key(WP_REST_Request $request) {
    $key = (string) get_option('kf_inbound_key');
    $given = (string) $request->get_header('x_kf_key');
    if ($key === '' || $given === '' || !hash_equals($key, $given)) {
        return new WP_Error('kf_forbidden', '연결 키가 맞지 않습니다.', array('status' => 403));
    }
    return true;
}

add_action('rest_api_init', function () {
    $auth = array('permission_callback' => 'kf_inbound_check_key');

    register_rest_route(KF_INBOUND_NS, '/ping', $auth + array(
        'methods'  => 'GET',
        'callback' => function () {
            global $wpdb;
            $t = kf_inbound_tables();
            return array(
                'ok'      => true,
                'site'    => get_bloginfo('name'),
                'version' => KF_INBOUND_VERSION,
                'pending' => (int) $wpdb->get_var("SELECT COUNT(*) FROM {$t['inbound']} WHERE status = 'pending'"),
            );
        },
    ));

    // 미리보기용: 상태를 바꾸지 않고 대기 중인 입고만 돌려준다
    register_rest_route(KF_INBOUND_NS, '/pending', $auth + array(
        'methods'  => 'GET',
        'callback' => function () {
            global $wpdb;
            $rows = $wpdb->get_results(kf_inbound_select_sql("i.status = 'pending'"));
            return array(
                'template' => (string) get_option('kf_inbound_template', KF_INBOUND_DEFAULT_TEMPLATE),
                'line'     => (string) get_option('kf_inbound_line', KF_INBOUND_DEFAULT_LINE),
                'entries'  => array_map('kf_inbound_entry_payload', $rows),
            );
        },
    ));

    // 발송 시작: 대기 중인 입고를 '발송 중'으로 바꾸고 돌려준다 (두 번 보내지 않게)
    register_rest_route(KF_INBOUND_NS, '/claim', $auth + array(
        'methods'  => 'POST',
        'callback' => function () {
            global $wpdb;
            $t = kf_inbound_tables();
            $now = kf_inbound_now();
            // 채팅방이 지정되지 않은 고객의 입고는 보낼 곳이 없으므로 실패로 표시
            $wpdb->query($wpdb->prepare(
                "UPDATE {$t['inbound']} i LEFT JOIN {$t['customers']} c ON c.id = i.customer_id
                 SET i.status = 'failed', i.note = %s, i.sent_at = %s
                 WHERE i.status = 'pending' AND (c.id IS NULL OR c.room = '')",
                '카톡 방 이름이 지정되지 않은 고객입니다. [고객·채팅방]에서 방 이름을 넣고 다시 보내기를 누르세요.',
                $now
            ));
            $token = wp_generate_password(12, false, false);
            $wpdb->query($wpdb->prepare(
                "UPDATE {$t['inbound']} SET status = 'sending', claimed_at = %s, note = %s WHERE status = 'pending'",
                $now,
                'claim:' . $token
            ));
            $rows = $wpdb->get_results($wpdb->prepare(
                kf_inbound_select_sql("i.status = 'sending' AND i.note = %s"),
                'claim:' . $token
            ));
            $wpdb->query($wpdb->prepare(
                "UPDATE {$t['inbound']} SET note = '' WHERE status = 'sending' AND note = %s",
                'claim:' . $token
            ));
            return array(
                'template' => (string) get_option('kf_inbound_template', KF_INBOUND_DEFAULT_TEMPLATE),
                'line'     => (string) get_option('kf_inbound_line', KF_INBOUND_DEFAULT_LINE),
                'entries'  => array_map('kf_inbound_entry_payload', $rows),
            );
        },
    ));

    // 발송 결과: sent(완료) / failed(실패) / pending(보내지 않음 → 다음 발송 때 다시)
    register_rest_route(KF_INBOUND_NS, '/report', $auth + array(
        'methods'  => 'POST',
        'callback' => function (WP_REST_Request $request) {
            global $wpdb;
            $t = kf_inbound_tables();
            $results = $request->get_json_params();
            $results = isset($results['results']) && is_array($results['results']) ? $results['results'] : array();
            $done = 0;
            foreach ($results as $r) {
                $id = isset($r['id']) ? (int) $r['id'] : 0;
                $status = isset($r['status']) ? (string) $r['status'] : '';
                if (!$id || !in_array($status, array('sent', 'failed', 'pending'), true)) {
                    continue;
                }
                $note = isset($r['note']) ? sanitize_textarea_field((string) $r['note']) : '';
                $data = array('status' => $status, 'note' => $note);
                if ($status !== 'pending') {
                    $data['sent_at'] = kf_inbound_now();
                }
                // '발송 중'인 건만 결과를 받는다 (그 사이 사람이 취소·수정한 건은 건드리지 않음)
                $done += (int) $wpdb->update($t['inbound'], $data, array('id' => $id, 'status' => 'sending'));
            }
            return array('ok' => true, 'updated' => $done);
        },
    ));

    // 사진 내려받기 (연결 키가 있어야 받을 수 있음)
    register_rest_route(KF_INBOUND_NS, '/photo/(?P<id>\d+)', $auth + array(
        'methods'  => 'GET',
        'callback' => function (WP_REST_Request $request) {
            $id = (int) $request['id'];
            $file = get_attached_file($id);
            if (get_post_type($id) !== 'attachment' || !$file || !file_exists($file)) {
                return new WP_Error('kf_not_found', '사진이 없습니다.', array('status' => 404));
            }
            $type = wp_check_filetype($file);
            nocache_headers();
            header('Content-Type: ' . ($type['type'] ? $type['type'] : 'application/octet-stream'));
            header('Content-Length: ' . filesize($file));
            readfile($file);
            exit;
        },
    ));
});

// ───────────────────────── 관리자 화면 ─────────────────────────
add_action('admin_menu', function () {
    add_menu_page('입고 알림', '입고 알림', KF_INBOUND_CAP, 'kf-inbound', 'kf_inbound_page_main', 'dashicons-format-gallery', 26);
    add_submenu_page('kf-inbound', '입고 등록·목록', '입고 등록·목록', KF_INBOUND_CAP, 'kf-inbound', 'kf_inbound_page_main');
    add_submenu_page('kf-inbound', '고객·채팅방', '고객·채팅방', KF_INBOUND_CAP, 'kf-inbound-customers', 'kf_inbound_page_customers');
    add_submenu_page('kf-inbound', '문구·연결 설정', '문구·연결 설정', KF_INBOUND_ADMIN_CAP, 'kf-inbound-settings', 'kf_inbound_page_settings');
});

function kf_inbound_notice($message, $type = 'success') {
    printf('<div class="notice notice-%s is-dismissible"><p>%s</p></div>', esc_attr($type), wp_kses_post($message));
}

/** 여러 장 올린 사진을 미디어 라이브러리에 저장하고 첨부 ID 목록을 돌려준다. */
function kf_inbound_handle_photos($field) {
    if (empty($_FILES[$field]) || !is_array($_FILES[$field]['name'])) {
        return array(array(), array());
    }
    require_once ABSPATH . 'wp-admin/includes/file.php';
    require_once ABSPATH . 'wp-admin/includes/media.php';
    require_once ABSPATH . 'wp-admin/includes/image.php';

    $ids = array();
    $errors = array();
    $files = $_FILES[$field];
    foreach ($files['name'] as $i => $name) {
        if ($files['error'][$i] === UPLOAD_ERR_NO_FILE || $name === '') {
            continue;
        }
        $_FILES['kf_inbound_single'] = array(
            'name'     => $files['name'][$i],
            'type'     => $files['type'][$i],
            'tmp_name' => $files['tmp_name'][$i],
            'error'    => $files['error'][$i],
            'size'     => $files['size'][$i],
        );
        $id = media_handle_upload('kf_inbound_single', 0, array(), array(
            'test_form' => false,
            'mimes'     => array(
                'jpg|jpeg|jpe' => 'image/jpeg',
                'png'          => 'image/png',
                'gif'          => 'image/gif',
                'webp'         => 'image/webp',
            ),
        ));
        if (is_wp_error($id)) {
            $errors[] = $name . ': ' . $id->get_error_message();
        } else {
            $ids[] = (int) $id;
        }
    }
    unset($_FILES['kf_inbound_single']);
    return array($ids, $errors);
}

function kf_inbound_handle_main_post() {
    global $wpdb;
    $t = kf_inbound_tables();
    if (empty($_POST['kf_action']) || !current_user_can(KF_INBOUND_CAP)) {
        return;
    }
    check_admin_referer('kf_inbound_main');
    $action = sanitize_key($_POST['kf_action']);

    if ($action === 'add') {
        $name = sanitize_text_field(wp_unslash($_POST['customer'] ?? ''));
        $customer = kf_inbound_customer_by_name($name);
        if (!$customer) {
            kf_inbound_notice('고객 <b>' . esc_html($name) . '</b> 이(가) 목록에 없습니다. [고객·채팅방]에서 먼저 추가하거나 목록에서 골라 주세요.', 'error');
            return;
        }
        $items = sanitize_textarea_field(wp_unslash($_POST['items'] ?? ''));
        $date = sanitize_text_field(wp_unslash($_POST['received_on'] ?? ''));
        if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $date)) {
            $date = current_time('Y-m-d');
        }
        list($photo_ids, $errors) = kf_inbound_handle_photos('photos');
        if ($items === '' && !$photo_ids) {
            kf_inbound_notice('내용이나 사진 중 하나는 있어야 합니다.' . ($errors ? '<br>' . esc_html(implode(' / ', $errors)) : ''), 'error');
            return;
        }
        $wpdb->insert($t['inbound'], array(
            'customer_id' => (int) $customer->id,
            'received_on' => $date,
            'items'       => $items,
            'photo_ids'   => implode(',', $photo_ids),
            'status'      => 'pending',
            'note'        => '',
            'created_by'  => get_current_user_id(),
            'created_at'  => kf_inbound_now(),
        ));
        $msg = '입고를 등록했습니다: <b>' . esc_html($customer->name) . '</b> (사진 ' . count($photo_ids) . '장). 예약 시간에 카톡으로 발송됩니다.';
        if ($customer->room === '') {
            $msg .= '<br>⚠️ 이 고객은 카톡 방 이름이 비어 있어 발송되지 않습니다. [고객·채팅방]에서 넣어 주세요.';
        }
        if ($errors) {
            $msg .= '<br>⚠️ 올리지 못한 사진: ' . esc_html(implode(' / ', $errors));
        }
        kf_inbound_notice($msg, $errors || $customer->room === '' ? 'warning' : 'success');
        return;
    }

    $id = (int) ($_POST['id'] ?? 0);
    if (!$id) {
        return;
    }
    if ($action === 'cancel') {
        $wpdb->update($t['inbound'], array('status' => 'cancelled'), array('id' => $id, 'status' => 'pending'));
        kf_inbound_notice('발송을 취소했습니다.');
    } elseif ($action === 'requeue') {
        $wpdb->update($t['inbound'], array('status' => 'pending', 'note' => '', 'sent_at' => null), array('id' => $id));
        kf_inbound_notice('다시 보내기로 바꿨습니다. 다음 예약 시간에 발송됩니다.');
    } elseif ($action === 'delete') {
        $wpdb->delete($t['inbound'], array('id' => $id));
        kf_inbound_notice('삭제했습니다. (사진은 미디어 라이브러리에 남아 있습니다)');
    }
}

function kf_inbound_page_main() {
    global $wpdb;
    $t = kf_inbound_tables();
    kf_inbound_handle_main_post();

    $customers = kf_inbound_customers(true);
    $status_filter = isset($_GET['status']) ? sanitize_key($_GET['status']) : '';
    $where = $status_filter && isset(KF_INBOUND_STATUS[$status_filter])
        ? $wpdb->prepare('i.status = %s', $status_filter)
        : '1=1';
    $rows = $wpdb->get_results(str_replace('ORDER BY i.id ASC', 'ORDER BY i.id DESC LIMIT 200', kf_inbound_select_sql($where)));
    $pending = (int) $wpdb->get_var("SELECT COUNT(*) FROM {$t['inbound']} WHERE status = 'pending'");
    $page_url = admin_url('admin.php?page=kf-inbound');
    ?>
    <div class="wrap">
        <h1>📦 입고 등록</h1>
        <p>입고를 등록해 두면 사무실 PC 발송 프로그램이 <b>예약 시간(예: 매일 17시)</b>에 고객별로 모아 사진과 함께 카톡으로 보냅니다.
            지금 발송 대기: <b><?php echo esc_html($pending); ?>건</b></p>

        <form method="post" enctype="multipart/form-data" style="background:#fff;padding:16px 20px;border:1px solid #dcdcde;max-width:720px">
            <?php wp_nonce_field('kf_inbound_main'); ?>
            <input type="hidden" name="kf_action" value="add">
            <table class="form-table" role="presentation">
                <tr>
                    <th><label for="kf-customer">고객</label></th>
                    <td>
                        <input id="kf-customer" name="customer" list="kf-customer-list" class="regular-text" style="max-width:100%" required autocomplete="off"
                               placeholder="고객명을 입력하면 목록이 나옵니다">
                        <datalist id="kf-customer-list">
                            <?php foreach ($customers as $c) : ?>
                                <option value="<?php echo esc_attr($c->name); ?>"><?php echo esc_html($c->room); ?></option>
                            <?php endforeach; ?>
                        </datalist>
                        <?php if (!$customers) : ?>
                            <p class="description">⚠️ 등록된 고객이 없습니다. <a href="<?php echo esc_url(admin_url('admin.php?page=kf-inbound-customers')); ?>">[고객·채팅방]</a>에서 먼저 추가하세요.</p>
                        <?php endif; ?>
                    </td>
                </tr>
                <tr>
                    <th><label for="kf-date">입고일</label></th>
                    <td><input id="kf-date" type="date" name="received_on" value="<?php echo esc_attr(current_time('Y-m-d')); ?>"></td>
                </tr>
                <tr>
                    <th><label for="kf-items">내용</label></th>
                    <td><textarea id="kf-items" name="items" rows="4" class="large-text" placeholder="예) 냉동 삼겹살 20박스 / 350kg"></textarea></td>
                </tr>
                <tr>
                    <th><label for="kf-photos">사진</label></th>
                    <td>
                        <input id="kf-photos" type="file" name="photos[]" accept="image/*" multiple>
                        <p class="description">여러 장 선택할 수 있습니다. 핸드폰에서는 바로 촬영도 됩니다.</p>
                    </td>
                </tr>
            </table>
            <?php submit_button('입고 등록', 'primary', 'submit', false); ?>
        </form>

        <h2 style="margin-top:32px">입고 목록</h2>
        <ul class="subsubsub">
            <li><a href="<?php echo esc_url($page_url); ?>" <?php echo $status_filter === '' ? 'class="current"' : ''; ?>>전체</a> | </li>
            <?php $last = array_key_last(KF_INBOUND_STATUS); foreach (KF_INBOUND_STATUS as $key => $label) : ?>
                <li><a href="<?php echo esc_url(add_query_arg('status', $key, $page_url)); ?>" <?php echo $status_filter === $key ? 'class="current"' : ''; ?>><?php echo esc_html($label); ?></a><?php echo $key === $last ? '' : ' | '; ?></li>
            <?php endforeach; ?>
        </ul>
        <div style="overflow-x:auto;clear:both">
        <table class="widefat striped" style="min-width:760px">
            <thead><tr><th>등록</th><th>고객 / 카톡 방</th><th>입고일</th><th>내용</th><th>사진</th><th>상태</th><th></th></tr></thead>
            <tbody>
            <?php if (!$rows) : ?>
                <tr><td colspan="7">입고 내역이 없습니다.</td></tr>
            <?php endif; ?>
            <?php foreach ($rows as $r) :
                $photos = kf_inbound_photo_ids($r);
                $stale = $r->status === 'sending' && $r->claimed_at && strtotime($r->claimed_at) < strtotime(kf_inbound_now()) - 3600;
                ?>
                <tr>
                    <td><?php echo esc_html(substr($r->created_at, 5, 11)); ?></td>
                    <td><b><?php echo esc_html($r->c_name ?: '(삭제된 고객)'); ?></b><br><small><?php echo esc_html($r->c_room ?: '⚠️ 방 이름 없음'); ?></small></td>
                    <td><?php echo esc_html($r->received_on); ?></td>
                    <td><?php echo nl2br(esc_html($r->items)); ?></td>
                    <td>
                        <?php foreach (array_slice($photos, 0, 4) as $pid) {
                            echo wp_get_attachment_image($pid, array(48, 48), false, array('style' => 'margin-right:2px'));
                        }
                        if (count($photos) > 4) {
                            echo esc_html(' +' . (count($photos) - 4));
                        } ?>
                    </td>
                    <td>
                        <?php echo esc_html(KF_INBOUND_STATUS[$r->status] ?? $r->status); ?>
                        <?php if ($stale) : ?><br><small>⚠️ 1시간 넘게 결과가 없습니다. 카톡에서 확인 후 필요하면 다시 보내기</small><?php endif; ?>
                        <?php if ($r->sent_at) : ?><br><small><?php echo esc_html(substr($r->sent_at, 5, 11)); ?></small><?php endif; ?>
                        <?php if ($r->note) : ?><br><small><?php echo esc_html($r->note); ?></small><?php endif; ?>
                    </td>
                    <td style="white-space:nowrap">
                        <?php
                        $buttons = array();
                        if ($r->status === 'pending') {
                            $buttons['cancel'] = '취소';
                        }
                        if (in_array($r->status, array('failed', 'cancelled', 'sent'), true) || $stale) {
                            $buttons['requeue'] = '다시 보내기';
                        }
                        if ($r->status !== 'sending') {
                            $buttons['delete'] = '삭제';
                        }
                        foreach ($buttons as $act => $label) : ?>
                            <form method="post" style="display:inline" <?php echo $act === 'delete' ? 'onsubmit="return confirm(\'삭제할까요?\')"' : ''; ?>>
                                <?php wp_nonce_field('kf_inbound_main'); ?>
                                <input type="hidden" name="kf_action" value="<?php echo esc_attr($act); ?>">
                                <input type="hidden" name="id" value="<?php echo esc_attr($r->id); ?>">
                                <button class="button button-small"><?php echo esc_html($label); ?></button>
                            </form>
                        <?php endforeach; ?>
                    </td>
                </tr>
            <?php endforeach; ?>
            </tbody>
        </table>
        </div>
    </div>
    <?php
}

function kf_inbound_page_customers() {
    global $wpdb;
    $t = kf_inbound_tables();
    if (!empty($_POST['kf_action']) && current_user_can(KF_INBOUND_CAP)) {
        check_admin_referer('kf_inbound_customers');
        $action = sanitize_key($_POST['kf_action']);
        if ($action === 'save') {
            $name = sanitize_text_field(wp_unslash($_POST['name'] ?? ''));
            $room = sanitize_text_field(wp_unslash($_POST['room'] ?? ''));
            if ($name === '') {
                kf_inbound_notice('고객명을 입력하세요.', 'error');
            } else {
                kf_inbound_save_customer((int) ($_POST['id'] ?? 0), $name, $room, sanitize_key($_POST['tab'] ?? 'chats'),
                    sanitize_text_field(wp_unslash($_POST['memo'] ?? '')));
                kf_inbound_notice('저장했습니다: ' . esc_html($name));
            }
        } elseif ($action === 'bulk') {
            $lines = preg_split('/\r\n|\r|\n/', wp_unslash($_POST['bulk'] ?? ''));
            $count = 0;
            foreach ($lines as $line) {
                $parts = array_map('trim', strpos($line, "\t") !== false ? explode("\t", $line) : explode('|', $line));
                $name = sanitize_text_field($parts[0] ?? '');
                if ($name === '') {
                    continue;
                }
                $room = sanitize_text_field($parts[1] ?? '') ?: $name;
                $count += (int) kf_inbound_save_customer(0, $name, $room, 'chats');
            }
            kf_inbound_notice($count . '명을 추가·수정했습니다.');
        } elseif ($action === 'delete') {
            $id = (int) ($_POST['id'] ?? 0);
            $used = (int) $wpdb->get_var($wpdb->prepare("SELECT COUNT(*) FROM {$t['inbound']} WHERE customer_id = %d", $id));
            if ($used) {
                $wpdb->update($t['customers'], array('active' => 0), array('id' => $id));
                kf_inbound_notice('입고 기록이 있어 삭제 대신 숨김 처리했습니다.');
            } else {
                $wpdb->delete($t['customers'], array('id' => $id));
                kf_inbound_notice('삭제했습니다.');
            }
        } elseif ($action === 'restore') {
            $wpdb->update($t['customers'], array('active' => 1), array('id' => (int) ($_POST['id'] ?? 0)));
        }
    }
    $edit = null;
    if (!empty($_GET['edit'])) {
        $edit = $wpdb->get_row($wpdb->prepare("SELECT * FROM {$t['customers']} WHERE id = %d", (int) $_GET['edit']));
    }
    $customers = kf_inbound_customers();
    ?>
    <div class="wrap">
        <h1>👥 고객·채팅방</h1>
        <p>입고 알림을 받을 카톡 방 이름을 고객마다 지정합니다. <b>방 이름은 PC 카카오톡에 보이는 이름과 한 글자도 틀리지 않게</b> 넣어 주세요
            (발송 프로그램의 [카톡 이름 정리]에서 읽은 이름을 붙여 넣으면 정확합니다).</p>

        <div style="display:flex;gap:24px;flex-wrap:wrap;align-items:flex-start">
            <form method="post" style="background:#fff;padding:12px 20px;border:1px solid #dcdcde;flex:1;min-width:320px">
                <h2><?php echo $edit ? '고객 수정' : '고객 추가'; ?></h2>
                <?php wp_nonce_field('kf_inbound_customers'); ?>
                <input type="hidden" name="kf_action" value="save">
                <input type="hidden" name="id" value="<?php echo esc_attr($edit->id ?? 0); ?>">
                <p><label>고객명<br><input name="name" class="regular-text" required value="<?php echo esc_attr($edit->name ?? ''); ?>"></label></p>
                <p><label>카톡 방 이름<br><input name="room" class="regular-text" value="<?php echo esc_attr($edit->room ?? ''); ?>" placeholder="예) KF - 세부마트 [CEBU]"></label></p>
                <p>찾을 곳:
                    <label><input type="radio" name="tab" value="chats" <?php checked(($edit->tab ?? 'chats') !== 'friends'); ?>> 채팅 목록(단톡방 포함)</label>
                    <label><input type="radio" name="tab" value="friends" <?php checked(($edit->tab ?? '') === 'friends'); ?>> 친구 목록</label>
                </p>
                <p><label>메모<br><input name="memo" class="regular-text" value="<?php echo esc_attr($edit->memo ?? ''); ?>"></label></p>
                <?php submit_button($edit ? '수정' : '추가', 'primary', 'submit', false); ?>
                <?php if ($edit) : ?> <a class="button" href="<?php echo esc_url(admin_url('admin.php?page=kf-inbound-customers')); ?>">취소</a><?php endif; ?>
            </form>

            <form method="post" style="background:#fff;padding:12px 20px;border:1px solid #dcdcde;flex:1;min-width:320px">
                <h2>한꺼번에 추가</h2>
                <?php wp_nonce_field('kf_inbound_customers'); ?>
                <input type="hidden" name="kf_action" value="bulk">
                <p class="description">한 줄에 한 고객: <code>고객명 | 카톡 방 이름</code> (엑셀에서 두 열을 복사해 붙여 넣어도 됩니다).
                    방 이름을 비우면 고객명을 방 이름으로 씁니다. 같은 고객명은 덮어씁니다.</p>
                <textarea name="bulk" rows="8" class="large-text" placeholder="세부마트 | KF - 세부마트 [CEBU]&#10;마닐라푸드 | KF-마닐라푸드"></textarea>
                <?php submit_button('추가', 'secondary', 'submit', false); ?>
            </form>
        </div>

        <h2 style="margin-top:24px">고객 목록 (<?php echo count($customers); ?>)</h2>
        <table class="widefat striped">
            <thead><tr><th>고객명</th><th>카톡 방 이름</th><th>찾을 곳</th><th>메모</th><th></th></tr></thead>
            <tbody>
            <?php foreach ($customers as $c) : ?>
                <tr style="<?php echo $c->active ? '' : 'opacity:.5'; ?>">
                    <td><?php echo esc_html($c->name); ?><?php echo $c->active ? '' : ' (숨김)'; ?></td>
                    <td><?php echo $c->room !== '' ? esc_html($c->room) : '⚠️ 비어 있음'; ?></td>
                    <td><?php echo $c->tab === 'friends' ? '친구' : '채팅'; ?></td>
                    <td><?php echo esc_html($c->memo); ?></td>
                    <td style="white-space:nowrap">
                        <a class="button button-small" href="<?php echo esc_url(add_query_arg('edit', $c->id, admin_url('admin.php?page=kf-inbound-customers'))); ?>">수정</a>
                        <form method="post" style="display:inline" <?php echo $c->active ? 'onsubmit="return confirm(\'삭제할까요?\')"' : ''; ?>>
                            <?php wp_nonce_field('kf_inbound_customers'); ?>
                            <input type="hidden" name="kf_action" value="<?php echo $c->active ? 'delete' : 'restore'; ?>">
                            <input type="hidden" name="id" value="<?php echo esc_attr($c->id); ?>">
                            <button class="button button-small"><?php echo $c->active ? '삭제' : '다시 사용'; ?></button>
                        </form>
                    </td>
                </tr>
            <?php endforeach; ?>
            </tbody>
        </table>
    </div>
    <?php
}

function kf_inbound_page_settings() {
    if (!current_user_can(KF_INBOUND_ADMIN_CAP)) {
        return;
    }
    if (!empty($_POST['kf_action'])) {
        check_admin_referer('kf_inbound_settings');
        if ($_POST['kf_action'] === 'save') {
            update_option('kf_inbound_template', sanitize_textarea_field(wp_unslash($_POST['template'] ?? '')) ?: KF_INBOUND_DEFAULT_TEMPLATE);
            update_option('kf_inbound_line', sanitize_text_field(wp_unslash($_POST['line'] ?? '')) ?: KF_INBOUND_DEFAULT_LINE);
            kf_inbound_notice('문구를 저장했습니다. 다음 발송부터 적용됩니다.');
        } elseif ($_POST['kf_action'] === 'newkey') {
            update_option('kf_inbound_key', wp_generate_password(40, false, false), false);
            kf_inbound_notice('새 연결 키를 만들었습니다. 사무실 PC 발송 프로그램에도 새 키를 넣어 주세요.', 'warning');
        }
    }
    $show_key = !empty($_GET['show_key']);
    ?>
    <div class="wrap">
        <h1>⚙️ 문구·연결 설정</h1>
        <form method="post" style="background:#fff;padding:12px 20px;border:1px solid #dcdcde;max-width:820px">
            <?php wp_nonce_field('kf_inbound_settings'); ?>
            <input type="hidden" name="kf_action" value="save">
            <h2>카톡 안내 문구</h2>
            <p class="description">고객별로 그날 입고를 모아 한 번에 보냅니다. 쓸 수 있는 값:
                <code>#{고객명}</code> <code>#{입고목록}</code> <code>#{건수}</code> <code>#{사진수}</code> <code>#{날짜}</code>(보내는 날)</p>
            <textarea name="template" rows="10" class="large-text code"><?php echo esc_textarea(get_option('kf_inbound_template', KF_INBOUND_DEFAULT_TEMPLATE)); ?></textarea>
            <p><label>#{입고목록} 한 줄 서식 (입고 1건마다):<br>
                <input name="line" class="large-text code" value="<?php echo esc_attr(get_option('kf_inbound_line', KF_INBOUND_DEFAULT_LINE)); ?>"></label></p>
            <p class="description">한 줄 서식에 쓸 수 있는 값: <code>#{입고일}</code> <code>#{내용}</code> <code>#{사진수}</code></p>
            <?php submit_button('문구 저장', 'primary', 'submit', false); ?>
        </form>

        <div style="background:#fff;padding:12px 20px;border:1px solid #dcdcde;max-width:820px;margin-top:20px">
            <h2>사무실 PC 발송 프로그램 연결</h2>
            <p>발송 프로그램의 <b>[입고 알림]</b> 메뉴에 아래 두 값을 넣으세요.</p>
            <table class="form-table" role="presentation">
                <tr><th>사이트 주소</th><td><code><?php echo esc_html(home_url('/')); ?></code></td></tr>
                <tr><th>연결 키</th><td>
                    <?php if ($show_key) : ?>
                        <code style="user-select:all"><?php echo esc_html(get_option('kf_inbound_key')); ?></code>
                        <p class="description">이 키가 있으면 입고 사진·고객 정보를 받을 수 있으니 다른 사람에게 알려 주지 마세요.</p>
                    <?php else : ?>
                        <a class="button" href="<?php echo esc_url(add_query_arg('show_key', 1)); ?>">키 보기</a>
                    <?php endif; ?>
                </td></tr>
            </table>
            <form method="post" onsubmit="return confirm('새 키를 만들면 지금 키로는 연결이 끊깁니다. 계속할까요?')">
                <?php wp_nonce_field('kf_inbound_settings'); ?>
                <input type="hidden" name="kf_action" value="newkey">
                <?php submit_button('새 연결 키 만들기', 'secondary', 'submit', false); ?>
            </form>
        </div>
    </div>
    <?php
}
