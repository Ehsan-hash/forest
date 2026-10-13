import Editor, { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor/esm/vs/editor/editor.api";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import { useUI } from "../ui";
import { useRef, useEffect } from "react";
(globalThis as any).MonacoEnvironment = { getWorker: () => new EditorWorker() };
loader.config({ monaco });
export default function CodeEditor({
  value,
  onChange,
  language = "python",
  height = "100%",
  readOnly = false,
  sessionKey,
}: {
  value: string;
  onChange?: (v: string) => void;
  language?: string;
  height?: string;
  readOnly?: boolean;
  sessionKey?: string;
}) {
  const { theme } = useUI();
  const session = useRef(sessionKey);
  session.current = sessionKey;
  const cleanupRef = useRef<(() => void) | null>(null);
  useEffect(
    () => () => {
      cleanupRef.current?.();
      cleanupRef.current = null;
    },
    [],
  );
  return (
    <Editor
      height={height}
      value={value}
      onChange={(v) => onChange?.(v || "")}
      language={language}
      path={
        sessionKey
          ? `forest-editor:///${encodeURIComponent(sessionKey)}`
          : undefined
      }
      saveViewState
      onMount={(editor) => {
        const persist = () => {
          const key = session.current;
          if (!key) return;
          // A model switch must never write the previous model under the next key.
          if (
            editor.getModel()?.uri.toString() !==
            monaco.Uri.parse(
              `forest-editor:///${encodeURIComponent(key)}`,
            ).toString()
          )
            return;
          try {
            sessionStorage.setItem(
              `forest-editor-view:${key}`,
              JSON.stringify(editor.saveViewState()),
            );
          } catch {
            /* Editing is available without browser storage. */
          }
        };
        if (session.current) {
          try {
            const saved = JSON.parse(
              sessionStorage.getItem(`forest-editor-view:${session.current}`) ||
                "null",
            );
            if (saved) editor.restoreViewState(saved);
          } catch {
            /* Ignore an unreadable caret/scroll snapshot. */
          }
        }
        const cursor = editor.onDidChangeCursorSelection(persist);
        const scroll = editor.onDidScrollChange(persist);
        cleanupRef.current = () => {
          persist();
          cursor.dispose();
          scroll.dispose();
        };
      }}
      theme={theme === "dark" ? "vs-dark" : "vs"}
      options={{
        fontSize: 12,
        fontFamily: '"SFMono-Regular", Consolas, monospace',
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        padding: { top: 16 },
        wordWrap: "on",
        automaticLayout: true,
        readOnly,
      }}
    />
  );
}
