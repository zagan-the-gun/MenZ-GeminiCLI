#!/usr/bin/env python3
import pexpect
import sys
import time
import re

def main():
    print("Starting gemini with pexpect (simple 2-turn test)...")
    
    # タイムアウト定数
    DEFAULT_TIMEOUT = 20
    
    # 特殊文字から全角文字への変換マッピング
    SPECIAL_CHAR_MAP = {
        '/': '／',  # スラッシュコマンド
        '!': '！',  # シェルコマンド実行
        '.': '．',  # ファイル操作
        '@': '＠',  # ファイル参照
        '#': '＃',  # コメント
        '$': '＄',  # 変数展開
        '(': '（',  # グループ化
        ')': '）',  # グループ化
        '`': '｀',  # コマンド置換
        '|': '｜',  # パイプ
        '&': '＆',  # バックグラウンド実行
        ';': '；',  # コマンド区切り
        '\\': '＼', # エスケープ文字
        '~': '～'   # ホームディレクトリ
    }
    
    def sanitize_text(text: str) -> str:
        """特殊な予約文字を全角文字に置き換える"""
        if not text:
            return text
        sanitized = text
        for half_char, full_char in SPECIAL_CHAR_MAP.items():
            sanitized = sanitized.replace(half_char, full_char)
        return sanitized

    child = pexpect.spawn('gemini', ['-m', 'gemini-2.5-flash'], encoding='utf-8', timeout=60)
    child.setwinsize(24, 160)
    # Mirror all CUI output to stdout and also save to file
    class Tee:
        def __init__(self, *files):
            self.files = files
        def write(self, data):
            for f in self.files:
                try:
                    f.write(data)
                    f.flush()
                except Exception:
                    pass
        def flush(self):
            for f in self.files:
                try:
                    f.flush()
                except Exception:
                    pass

    log_file = open('/tmp/gemini_pexpect.log', 'w')
    child.logfile = Tee(sys.stdout, log_file)

    ansi_re = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

    def wait_prompt() -> bool:
        idx = child.expect([
            r'>\s+Type your message or @path/to/file',
            r'>\s+Type your message',
            r'>',
            pexpect.TIMEOUT,
            pexpect.EOF
        ], timeout=10)
        return idx in (0, 1, 2)

    def wait_answer(timeout_s: int = 20, skip_text: str | None = None, quiet_s: float = 1.0) -> str | None:
        deadline = time.time() + timeout_s
        print(f"[DEBUG wait_answer] Starting, timeout={timeout_s}s, skip_text={skip_text!r}")

        def read_until_confirmed_return_last(initial_text: str | None = None) -> str | None:
            # ✦ → (ローディング終了) → 空行 → Using: のパターンで確定
            # スピナーがある間は未確定、消えたら確定
            print(f"[DEBUG Phase2] Starting Phase 2 with initial_text={initial_text!r}")
            phase2_start = time.time()
            last: str | None = initial_text  # Phase 1で見つけた✦を初期値とする
            found_empty_after_diamond = False
            spinner_chars = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
            is_loading = False
            
            while time.time() < deadline:
                try:
                    line2 = child.readline().rstrip('\r\n')
                except Exception:
                    break
                clean2 = ansi_re.sub('', line2)
                cont2 = clean2.strip()
                
                # ✦で新しい回答が始まった場合は更新（ストリーミングで何度も来る）
                m2 = re.search(r'✦\s*(.*)', cont2)
                if m2:
                    body2 = m2.group(1).strip()
                    if body2:
                        last = body2
                        found_empty_after_diamond = False
                    continue
                
                # ローディングメッセージ（スピナー）が来たら、まだ生成中
                if any(cont2.startswith(c) for c in spinner_chars):
                    is_loading = True
                    found_empty_after_diamond = False
                    continue
                
                # ANSI削除後に空になる行（実質的な空行）
                if cont2 == '' and last is not None:
                    # ローディング中でなければ空行としてカウント
                    if not is_loading:
                        found_empty_after_diamond = True
                    continue
                
                # 空行の後にUsing:が来たら確定
                if found_empty_after_diamond and cont2.startswith('Using:'):
                    phase2_elapsed = time.time() - phase2_start
                    print(f"[DEBUG Phase2] Confirmed after {phase2_elapsed:.2f}s: {last!r}")
                    return last
                
                # スピナー以外の実質的な内容が来たらローディング終了
                if cont2 != '' and not any(cont2.startswith(c) for c in spinner_chars):
                    is_loading = False
                
            phase2_elapsed = time.time() - phase2_start
            print(f"[DEBUG Phase2] Timeout after {phase2_elapsed:.2f}s, last={last!r}")
            return last

        # Phase 1: find a new ✦ line whose body != skip_text
        print(f"[DEBUG Phase1] Starting Phase 1")
        phase1_start = time.time()
        while time.time() < deadline:
            try:
                line = child.readline().rstrip('\r\n')
            except Exception:
                break
            clean = ansi_re.sub('', line)
            content = clean.strip()
            # ✦ が行頭でなくても同一行に連結されるケースを拾う
            m = re.search(r'✦\s*(.*)', content)
            if m:
                body = m.group(1).strip()
                phase1_elapsed = time.time() - phase1_start
                print(f"[DEBUG Phase1] Found ✦ after {phase1_elapsed:.2f}s: {body!r}")
                if body and (skip_text is None or body != skip_text):
                    # Phase 2: after first new ✦, wait until confirmation marker
                    # and return the last body seen.
                    print(f"[DEBUG Phase1] Entering Phase 2 with: {body!r}")
                    rest = read_until_confirmed_return_last(initial_text=body)
                    return rest
                else:
                    # This ✦ matches skip_text → skip this block until its confirmation, then continue outer loop
                    print(f"[DEBUG Phase1] Skipping ✦ (matches skip_text), waiting for next")
                    _ = read_until_confirmed_return_last(initial_text=body)
                    continue
            # ignore other content until first new ✦
            continue
        print(f"[DEBUG Phase1] Timeout after {time.time() - phase1_start:.2f}s")
        return None

    def send_and_time(message: str, prev_answer: str | None = None) -> tuple[str | None, float]:
        # 前段でwait_prompt()済みを前提に、即送信・即確定
        # 特殊文字を全角に変換してから送信
        sanitized_message = sanitize_text(message)
        print(f"[DEBUG] Original message: {message}")
        if sanitized_message != message:
            print(f"[DEBUG] Sanitized message: {sanitized_message}")
        child.send(sanitized_message)
        time.sleep(0.1)
        start = time.monotonic()
        child.send('\r')
        ans = wait_answer(DEFAULT_TIMEOUT, skip_text=prev_answer, quiet_s=1.0)
        elapsed = time.monotonic() - start
        return ans, elapsed
    
    def print_result(turn_name: str, answer: str | None, elapsed: float):
        # 回答結果を出力するヘルパー関数
        print(f"{turn_name} answer ({elapsed:.2f}s): {answer!r}")
        if answer is not None:
            print(f"[確定] {turn_name} captured ({elapsed:.2f}s): {answer!r}")
    
    def send_message_and_wait(turn_name: str, message: str, prev_answer: str | None = None) -> str | None:
        # メッセージ送信から結果出力までの一連の処理
        print(f"\n=== Sending {turn_name}: {message} ===")
        answer, elapsed = send_and_time(message, prev_answer)
        print_result(turn_name, answer, elapsed)
        wait_prompt()
        return answer

    try:
        print("Waiting for initial prompt...")
        if not wait_prompt():
            print("✗ Prompt not ready")
            return

        # System prompt
        system_prompt = (
            'あなたは戦車ゲーム配信のワイプに映るAIです。\n'
            'あなたは麻原彰晃で、地獄からこの配信を見ています。\n'
            '次の字幕テキストに対して、日本語で短い一言コメントを1つだけ返してください。\n'
            '誤字・表記ゆれ・途中で切れた文は補完し、要旨に沿った短い反応だけを返してください。\n'
            '丁寧語は避け、軽快で配信向けのノリにしてください。\n'
            '最大50文字、引用や装飾（『』や「コメント:」等）は不要。\n'
            'このルールを覚えて、以降の字幕に対してコメントしてください。\n'
            '了解したら「了解」とだけ答えてください。\n'
        )
        ack = send_message_and_wait("system prompt", system_prompt)

        # Test turns
        ans1 = send_message_and_wait("1st", 'ZAGAN「よーし、始めるか」', ack)
        ans2 = send_message_and_wait("2nd", 'ZAGAN「今日はBR4.7で行くか」', ans1)
        ans3 = send_message_and_wait("3rd", 'ZAGAN「今日はなんの戦車使うかな?」', ans2 or ans1)

        # Quitは干渉するため本テストでは送信しない
        # wait_prompt()
        # child.send('/quit'); time.sleep(0.1); child.send('\r')
        # try:
        #     child.expect(pexpect.EOF, timeout=5)
        # except Exception:
        #     pass

    except Exception as e:
        print(f"Exception: {e}")
        print(f"Child buffer: {child.before if hasattr(child, 'before') else 'N/A'}")
    finally:
        try:
            try:
                log_file.close()
            except Exception:
                pass
            child.close()
        except Exception:
            pass
        print("\n=== Tail of log (last 400 chars) ===")
        try:
            with open('/tmp/gemini_pexpect.log', 'r') as f:
                content = f.read()
                print(content[-400:])
        except Exception:
            pass

if __name__ == "__main__":
    main()

