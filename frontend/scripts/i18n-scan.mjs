// Находит русские строки в исходниках интерфейса. Используется и как отчёт, и как проверка: `node scripts/i18n-scan.mjs --check`.
import ts from 'typescript'
import fs from 'node:fs'
import path from 'node:path'

const CYR = /[А-Яа-яЁё]/
const root = path.resolve(import.meta.dirname, '../src')
const SKIP = [/locales\//, /i18n\.ts$/]

function* walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) yield* walk(p)
    else if (/\.(ts|tsx)$/.test(e.name) && !SKIP.some(r => r.test(p))) yield p
  }
}

export function scan(file) {
  const text = fs.readFileSync(file, 'utf8')
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, file.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
  const found = []
  const line = n => sf.getLineAndCharacterOfPosition(n.getStart()).line + 1
  const ignored = n => /i18n-ignore/.test(text.split('\n')[line(n) - 1] ?? '')
  // строка «под переводом»: это сам ключ — первый аргумент tr() или формы plural(); русские литералы среди аргументов подстановки не считаются
  const inT = n => {
    for (let c = n, p = n.parent; p; c = p, p = p.parent) {
      if (!ts.isCallExpression(p)) continue
      const callee = ts.isIdentifier(p.expression) ? p.expression.text : ts.isPropertyAccessExpression(p.expression) ? p.expression.name.text : ''
      const index = p.arguments.indexOf(c)
      if (callee === 'tr' && index === 0) return true
      if (callee === 'plural' && index >= 1 && index <= 3) return true
    }
    return false
  }
  const visit = n => {
    if (ignored(n)) return
    if (ts.isJsxText(n) && CYR.test(n.text)) found.push({ kind: 'jsx', text: n.text, node: n, line: line(n) })
    else if ((ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) && CYR.test(n.text) && !inT(n)) {
      const parent = n.parent
      if (ts.isImportDeclaration(parent) || ts.isExportDeclaration(parent)) return
      found.push({ kind: ts.isJsxAttribute(parent) ? 'attr' : 'str', text: n.text, node: n, line: line(n) })
    } else if (ts.isTemplateExpression(n) && !inT(n) && [n.head, ...n.templateSpans.map(s => s.literal)].some(l => CYR.test(l.text))) {
      found.push({ kind: 'tpl', text: n.getText(), node: n, line: line(n) })
    }
    ts.forEachChild(n, visit)
  }
  visit(sf)
  return { sf, text, found }
}

if (process.argv[1] === import.meta.filename) {
  const check = process.argv.includes('--check')
  const kinds = {}
  let total = 0
  const lines = []
  for (const f of walk(root)) {
    const { found } = scan(f)
    for (const x of found) {
      kinds[x.kind] = (kinds[x.kind] || 0) + 1; total++
      lines.push(`${path.relative(root, f)}:${x.line} [${x.kind}] ${x.text.trim().replace(/\s+/g, ' ').slice(0, 90)}`)
    }
  }
  if (check) {
    if (lines.length) { console.error('Русские строки вне t():\n' + lines.slice(0, 60).join('\n') + (lines.length > 60 ? `\n… и ещё ${lines.length - 60}` : '')); process.exit(1) }
    console.log('i18n: вшитых русских строк нет')
  } else {
    console.log(total, kinds)
    if (process.argv.includes('--list')) console.log(lines.join('\n'))
  }
}
