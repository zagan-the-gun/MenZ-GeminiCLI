import re
import logging
import asyncio
import time
from typing import Optional
import pexpect


class GeminiCLIRunner:
    """
    Wrapper around Gemini CLI in interactive mode using pexpect.
    
    Maintains a persistent session across multiple prompts.
    """

    # 特殊文字から全角文字への変換マッピング
    _SPECIAL_CHAR_MAP = {
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
    
    # スピナー文字（ローディング中の判定用）
    _SPINNER_CHARS = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'

    def __init__(
        self,
        model_name: str,
        timeout_seconds: int = 60,
        max_output_chars: int = 120,
        prompt_template: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> None:
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars
        self._logger = logging.getLogger("MenZ-GeminiCLI")
        self.prompt_template = prompt_template
        self.system_prompt = system_prompt
        
        self._child = None
        self._initialized = False
        self._ansi_re = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        self._last_answer: Optional[str] = None  # 前回の回答を保持

    async def _initialize_async(self) -> None:
        """Initialize pexpect session with Gemini CLI."""
        if self._initialized:
            self._logger.debug("already initialized, skipping")
            return
        
        self._logger.debug("initializing pexpect session...")
        
        # Start gemini with pexpect
        self._child = pexpect.spawn(
            'gemini',
            ['-m', self.model_name],
            encoding='utf-8',
            timeout=self.timeout_seconds
        )
        self._child.setwinsize(24, 160)
        
        # Set locale
        import os
        os.environ['LANG'] = 'ja_JP.UTF-8'
        os.environ['LC_ALL'] = 'ja_JP.UTF-8'
        
        self._logger.info("started interactive gemini session: gemini -m %s", self.model_name)
        
        # Wait for initial prompt
        try:
            index = self._child.expect([r'>.*Type your message', pexpect.TIMEOUT], timeout=10)
            if index == 0:
                self._logger.debug("input prompt ready")
            else:
                self._logger.warning("timeout waiting for initial prompt")
        except Exception as e:
            self._logger.error("failed to wait for initial prompt: %s", e)
        
        # Send system prompt if configured
        if self.system_prompt:
            await self._send_system_prompt_async()
        
        self._initialized = True
        self._logger.info("pexpect session initialized successfully")

    async def _wait_prompt(self) -> bool:
        """プロンプトが表示されるまで待つ"""
        try:
            idx = await asyncio.to_thread(
                self._child.expect,
                [
                    r'>\s+Type your message or @path/to/file',
                    r'>\s+Type your message',
                    r'>',
                    pexpect.TIMEOUT,
                    pexpect.EOF
                ],
                timeout=10
            )
            return idx in (0, 1, 2)
        except Exception as e:
            self._logger.warning("failed to wait for prompt: %s", e)
            return False

    async def _wait_answer(self, timeout_s: int = 20, skip_text: Optional[str] = None) -> Optional[str]:
        """
        AIの回答を待つ（ストリーミング対応、確定まで追跡）
        
        Args:
            timeout_s: タイムアウト秒数
            skip_text: スキップする回答テキスト（前回の回答など）
        
        Returns:
            確定した回答テキスト、またはNone
        """
        deadline = time.time() + timeout_s
        self._logger.debug("wait_answer: timeout=%ds, skip_text=%r", timeout_s, skip_text)

        async def read_until_confirmed_return_last(initial_text: Optional[str] = None) -> Optional[str]:
            """Phase 2: ストリーミング中の回答を追跡し、確定まで待つ"""
            self._logger.debug("Phase2: starting with initial_text=%r", initial_text)
            phase2_start = time.time()
            last: Optional[str] = initial_text
            found_empty_after_diamond = False
            is_loading = False
            
            while time.time() < deadline:
                try:
                    line2 = await asyncio.to_thread(self._child.readline)
                    line2 = line2.rstrip('\r\n')
                except Exception:
                    break
                
                clean2 = self._ansi_re.sub('', line2)
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
                if any(cont2.startswith(c) for c in self._SPINNER_CHARS):
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
                    self._logger.debug("Phase2: confirmed after %.2fs: %r", phase2_elapsed, last)
                    return last
                
                # スピナー以外の実質的な内容が来たらローディング終了
                if cont2 != '' and not any(cont2.startswith(c) for c in self._SPINNER_CHARS):
                    is_loading = False
            
            phase2_elapsed = time.time() - phase2_start
            self._logger.debug("Phase2: timeout after %.2fs, last=%r", phase2_elapsed, last)
            return last

        # Phase 1: 新しい✦行を探す（skip_textと異なる内容）
        self._logger.debug("Phase1: starting")
        phase1_start = time.time()
        while time.time() < deadline:
            try:
                line = await asyncio.to_thread(self._child.readline)
                line = line.rstrip('\r\n')
            except Exception:
                break
            
            clean = self._ansi_re.sub('', line)
            content = clean.strip()
            
            # ✦ が行頭でなくても同一行に連結されるケースを拾う
            m = re.search(r'✦\s*(.*)', content)
            if m:
                body = m.group(1).strip()
                phase1_elapsed = time.time() - phase1_start
                self._logger.debug("Phase1: found ✦ after %.2fs: %r", phase1_elapsed, body)
                
                if body and (skip_text is None or body != skip_text):
                    # Phase 2: 最初の新しい✦の後、確定マーカーまで待つ
                    self._logger.debug("Phase1: entering Phase 2 with: %r", body)
                    rest = await read_until_confirmed_return_last(initial_text=body)
                    return rest
                else:
                    # この✦はskip_textと一致 → このブロックの確定を待ってから外側ループを続行
                    self._logger.debug("Phase1: skipping ✦ (matches skip_text), waiting for next")
                    _ = await read_until_confirmed_return_last(initial_text=body)
                    continue
            # 最初の新しい✦まで他のコンテンツは無視
            continue
        
        phase1_elapsed = time.time() - phase1_start
        self._logger.debug("Phase1: timeout after %.2fs", phase1_elapsed)
        return None

    async def _send_system_prompt_async(self) -> None:
        """Send initial system prompt and wait for response."""
        self._logger.info("sending system prompt")
        try:
            # サニタイズしてから送信
            sanitized_prompt = self._sanitize_text(self.system_prompt)
            self._child.send(sanitized_prompt)
            await asyncio.sleep(0.1)
            self._child.send('\r')
            
            self._logger.debug("sent system prompt, waiting for answer...")
            
            # 改善された待機ロジックを使用
            answer = await self._wait_answer(timeout_s=20, skip_text=None)
            
            if answer:
                self._logger.info("system prompt response: %s", answer)
                self._last_answer = answer
            else:
                self._logger.warning("system prompt timed out")
            
            # プロンプトが戻るまで待つ
            await self._wait_prompt()
        except Exception as e:
            self._logger.warning("failed to process system prompt: %s", e)

    def _sanitize_text(self, text: str) -> str:
        """
        特殊な予約文字を全角文字に置き換える。
        これにより、意図しない機能の起動を防ぐ。
        """
        if not text:
            return text
        
        # 各特殊文字を全角に置き換え
        sanitized = text
        for half_char, full_char in self._SPECIAL_CHAR_MAP.items():
            sanitized = sanitized.replace(half_char, full_char)
        
        return sanitized

    def build_prompt(self, subtitle_text: str, speaker: Optional[str]) -> str:
        """Build prompt for subtitle text."""
        # 特殊文字を全角に変換してから使用
        sanitized_text = self._sanitize_text(subtitle_text)
        sanitized_speaker = self._sanitize_text(speaker) if speaker else None
        
        if self.prompt_template:
            speaker_part = f"（話者: {sanitized_speaker}）" if sanitized_speaker else ""
            lines_num = sanitized_text.count("\n") + 1 if sanitized_text else 0
            return self.prompt_template.format(
                text=sanitized_text,
                speaker=sanitized_speaker or "",
                speaker_part=speaker_part,
                lines_num=lines_num,
            )
        else:
            return f"{sanitized_speaker}「{sanitized_text}」" if sanitized_speaker else f"「{sanitized_text}」"

    async def _send_and_receive_async(self, prompt: str) -> str:
        """Send prompt and wait for response using pexpect."""
        if self._child is None or not self._initialized:
            raise RuntimeError("pexpect session is not initialized")
        
        try:
            self._logger.debug("sending prompt: %d chars", len(prompt))
            
            # サニタイズしてから送信
            sanitized_prompt = self._sanitize_text(prompt)
            if sanitized_prompt != prompt:
                self._logger.debug("sanitized prompt: special chars converted to full-width")
            
            # プロンプトを送信
            await asyncio.to_thread(self._child.send, sanitized_prompt)
            await asyncio.sleep(0.1)
            start_time = time.monotonic()
            await asyncio.to_thread(self._child.send, '\r')
            
            # 改善された待機ロジックを使用（前回の回答をスキップ）
            answer = await self._wait_answer(
                timeout_s=self.timeout_seconds,
                skip_text=self._last_answer
            )
            
            elapsed = time.monotonic() - start_time
            
            if answer:
                self._logger.debug("received response in %.2fs: %d chars", elapsed, len(answer))
                self._last_answer = answer  # 次回のためにキャッシュ
                
                # プロンプトが戻るまで待つ
                await self._wait_prompt()
                
                return answer
            else:
                raise TimeoutError(f"No response received within {self.timeout_seconds} seconds")
                
        except asyncio.CancelledError:
            self._logger.info("request cancelled")
            raise
        except Exception as e:
            self._logger.error("error in send_and_receive: %s", e)
            raise

    def _extract_comment(self, raw_output: str) -> str:
        # Remove ANSI escape sequences
        cleaned_output = self._ansi_re.sub("", raw_output)

        # Take the first non-empty line, strip common markdown fences and quotes
        for line in cleaned_output.splitlines():
            normalized = line.strip()
            if not normalized:
                continue
            # Remove code fences or markdown bullets if present
            if normalized.startswith("```") and normalized.endswith("```"):
                normalized = normalized.strip("`")
            # Trim leading markdown bullets/numbers
            if normalized.startswith(('- ', '* ', '1. ', '・', '✔ ', '✓ ', '» ')):
                normalized = normalized[2:].strip()
            # Remove common role prefixes
            for prefix in ("assistant:", "model:", "output:"):
                if normalized.lower().startswith(prefix):
                    normalized = normalized[len(prefix):].strip()
            # Remove wrapping quotes
            if (normalized.startswith('"') and normalized.endswith('"')) or (
                normalized.startswith("'") and normalized.endswith("'")
            ):
                normalized = normalized[1:-1].strip()

            # Enforce max length
            if self.max_output_chars > 0 and len(normalized) > self.max_output_chars:
                normalized = normalized[: self.max_output_chars]
            return normalized

        # If no lines were extractable, fallback to whole cleaned output (truncated)
        fallback = cleaned_output.strip()
        return fallback[: self.max_output_chars] if fallback else ""

    async def generate_comment_async(self, subtitle_text: str, speaker: Optional[str]) -> str:
        """Generate comment asynchronously (cancellable)."""
        prompt = self.build_prompt(subtitle_text=subtitle_text, speaker=speaker)
        
        self._logger.info("start generation: model=%s prompt_chars=%d", self.model_name, len(prompt))
        
        try:
            raw = await self._send_and_receive_async(prompt)
            comment = self._extract_comment(raw_output=raw)
            if not comment:
                self._logger.warning("no extractable comment; using fallback")
                return "いいね！"
            return comment
        except asyncio.CancelledError:
            self._logger.info("generation cancelled")
            raise
        except Exception as e:
            self._logger.error("generation error: %s", e)
            return "いいね！"

    def close(self) -> None:
        """Close the pexpect session."""
        self._logger.info("closing pexpect session...")
        
        if self._child:
            try:
                self._child.sendline("/quit")
                self._child.expect(pexpect.EOF, timeout=2)
            except:
                pass
            
            try:
                self._child.close(force=True)
            except:
                pass
            
            self._child = None
        
        self._initialized = False
        self._last_answer = None
        self._logger.debug("pexpect session closed")

    def __del__(self) -> None:
        """Cleanup on destruction."""
        if self._initialized:
            self.close()