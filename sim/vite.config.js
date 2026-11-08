import { defineConfig } from 'vite';

// Allow importing ../backend/boards/*.json (shared board profiles) from outside sim/.
export default defineConfig({
  server: { fs: { allow: ['..'] } },
});
