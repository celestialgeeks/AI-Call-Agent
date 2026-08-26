import { defineConfig } from 'vite';
import { resolve } from 'path';

/**
 * Vite Multi-Page Application (MPA) config.
 * Each HTML file at the root is an independent entry point.
 * Ref: https://vitejs.dev/guide/build.html#multi-page-app
 */
export default defineConfig({
    // Resolve aliases for clean imports inside src/
    resolve: {
        alias: {
            '@': resolve(__dirname, './src'),
        },
    },

    build: {
        rollupOptions: {
            input: {
                // Landing, Auth, Dashboard, and legal pages are independent entries
                main: resolve(__dirname, 'index.html'),
                auth: resolve(__dirname, 'auth.html'),
                dashboard: resolve(__dirname, 'app.html'),
                terms: resolve(__dirname, 'terms.html'),
                privacy: resolve(__dirname, 'privacy.html'),
            },
        },
        // Output clean filename in production
        chunkSizeWarningLimit: 600,
    },

    // Dev server settings
    server: {
        port: 5173,
        open: '/index.html',
    },

    // CSS source maps in dev
    css: {
        devSourcemap: true,
    },
});
