/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Dev/test only: core base URL, e.g. http://127.0.0.1:7400 */
  readonly VITE_MISES_URL?: string;
  /** Dev/test only: core bearer token */
  readonly VITE_MISES_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
