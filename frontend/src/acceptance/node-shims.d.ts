declare module 'node:fs' {
  export interface Dirent {
    name: string
    isDirectory(): boolean
  }
  export function readFileSync(path: string, encoding: string): string
  export function readdirSync(path: string): string[]
  export function readdirSync(path: string, options: { withFileTypes: true }): Dirent[]
}
declare module 'node:path' {
  export function resolve(...paths: string[]): string
}
declare const __dirname: string
