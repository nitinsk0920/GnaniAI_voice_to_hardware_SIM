import { defineConfig } from 'vite';

// Allow importing ../backend/board_uno.json (shared pin map) from outside sim/.
export default defineConfig({
  server: { fs: { allow: ['..'] } },
});
