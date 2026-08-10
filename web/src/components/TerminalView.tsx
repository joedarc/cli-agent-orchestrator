import { useEffect, useRef } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { X, Terminal as TermIcon } from 'lucide-react'

interface TerminalViewProps {
  terminalId: string
  provider?: string
  agentProfile?: string | null
  onClose: () => void
}

export function TerminalView({ terminalId, provider, agentProfile, onClose }: TerminalViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: 'JetBrains Mono, Menlo, Monaco, Consolas, monospace',
      scrollback: 10000,
      theme: {
        background: '#0d1117',
        foreground: '#c9d1d9',
        cursor: '#58a6ff',
        selectionBackground: '#1f3a5f',
        selectionForeground: '#c9d1d9',
        black: '#0d1117',
        red: '#3d1a1a',
        green: '#1a3d1a',
        yellow: '#e3b341',
        blue: '#58a6ff',
        magenta: '#d2a8ff',
        cyan: '#76e3ea',
        white: '#c9d1d9',
        brightBlack: '#484f58',
        brightRed: '#ff7b72',
        brightGreen: '#3fb950',
        brightYellow: '#e3b341',
        brightBlue: '#79c0ff',
        brightMagenta: '#d2a8ff',
        brightCyan: '#76e3ea',
        brightWhite: '#f0f6fc',
      },
    })

    const fitAddon = new FitAddon()
    term.loadAddon(fitAddon)
    term.open(el)

    // Connect WebSocket
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${location.host}/terminals/${terminalId}/ws`)
    ws.binaryType = 'arraybuffer'

    ws.onopen = () => {
      // Fit once the connection is live so we send correct dimensions
      fitAddon.fit()
      ws.send(JSON.stringify({ type: 'resize', rows: term.rows, cols: term.cols }))
    }

    ws.onmessage = (e) => {
      if (e.data instanceof ArrayBuffer) {
        if (provider === 'kiro_cli') {
          // Remap Kiro's light diff background colors to darker equivalents
          // so they're readable on a dark terminal background.
          let str = new TextDecoder().decode(new Uint8Array(e.data))
          str = str
            .replace(/\x1b\[48;2;212;240;212m/g, '\x1b[48;2;30;60;30m')   // light green → dark green
            .replace(/\x1b\[48;2;240;212;212m/g, '\x1b[48;2;60;25;25m')   // light pink → dark red
            .replace(/\x1b\[48;2;238;238;238m/g, '\x1b[48;2;40;40;40m')   // light grey → dark grey
          term.write(str)
        } else {
          // Pass raw bytes directly — no decode needed, xterm.js handles binary natively.
          term.write(new Uint8Array(e.data))
        }
      }
    }

    ws.onclose = () => {
      term.write('\r\n\x1b[33m[Connection closed]\x1b[0m\r\n')
    }

    // Copy selection to clipboard on mouse-up — debounced to avoid clipboard
    // API calls on every character of a drag selection, which causes input lag.
    let selectionTimer: ReturnType<typeof setTimeout>
    term.onSelectionChange(() => {
      clearTimeout(selectionTimer)
      selectionTimer = setTimeout(() => {
        const selection = term.getSelection()
        if (selection) {
          navigator.clipboard.writeText(selection).catch(() => {})
        }
      }, 200)
    })

    // Ctrl+Shift+C to copy selection
    term.attachCustomKeyEventHandler((e) => {
      if (e.ctrlKey && e.shiftKey && e.key === 'C') {
        const selection = term.getSelection()
        if (selection) navigator.clipboard.writeText(selection).catch(() => {})
        return false
      }
      return true
    })

    // onData handles ALL input including paste — xterm.js
    // receives pasted text through the browser's input system
    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'input', data }))
      }
    })

    // Handle resize — debounce to avoid flooding
    let resizeTimer: ReturnType<typeof setTimeout>
    const resizeObserver = new ResizeObserver(() => {
      clearTimeout(resizeTimer)
      resizeTimer = setTimeout(() => {
        fitAddon.fit()
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'resize', rows: term.rows, cols: term.cols }))
        }
      }, 50)
    })
    resizeObserver.observe(el)

    // Initial fit after layout settles
    const initialFit = requestAnimationFrame(() => {
      fitAddon.fit()
    })

    term.focus()

    return () => {
      cancelAnimationFrame(initialFit)
      clearTimeout(resizeTimer)
      clearTimeout(selectionTimer)
      resizeObserver.disconnect()
      ws.close()
      term.dispose()
    }
  }, [terminalId])

  return (
    <div className="fixed inset-0 z-50 flex flex-col" style={{ background: '#0d1117' }}>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-gray-900 border-b border-gray-700/50 shrink-0">
        <div className="flex items-center gap-3">
          <TermIcon size={16} className="text-emerald-400" />
          <span className="text-sm font-mono text-gray-300">{terminalId}</span>
          {provider && <span className="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded">{provider}</span>}
          {agentProfile && <span className="text-xs text-emerald-400 bg-emerald-900/30 px-2 py-0.5 rounded">{agentProfile}</span>}
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[10px] text-gray-600">Click X to close</span>
          <button
            onClick={onClose}
            className="p-1 text-gray-500 hover:text-white transition-colors rounded"
            title="Close terminal"
          >
            <X size={18} />
          </button>
        </div>
      </div>
      {/* Terminal — absolute positioning gives xterm.js real pixel dimensions to measure */}
      <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        <div ref={containerRef} style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }} />
      </div>
    </div>
  )
}
