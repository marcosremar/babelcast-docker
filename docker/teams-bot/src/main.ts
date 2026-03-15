import { server } from './server'
import {
    setupConsoleLogger,
    setupExitHandler,
} from './utils/Logger'

import { exit } from 'process'

// ========================================
// CONFIGURATION
// ========================================

// Setup console logger first to ensure proper formatting
setupConsoleLogger()

// Setup crash handlers to upload logs in case of unexpected exit
setupExitHandler()

// Configuration to enable/disable DEBUG logs
export const DEBUG_LOGS =
    process.argv.includes('--debug') || process.env.DEBUG_LOGS === 'true'
if (DEBUG_LOGS) {
    console.log('DEBUG mode activated - speakers debug logs will be shown')
    import('./browser/page-logger')
        .then(({ enablePrintPageLogs }) => enablePrintPageLogs())
        .catch((e) =>
            console.error('Failed to enable page logs dynamically:', e),
        )
}

// ========================================
// MAIN ENTRY POINT
// ========================================

;(async () => {
    try {
        // Start HTTP server immediately (health check available right away)
        await server().catch((e) => {
            console.error(`Failed to start server: ${e}`)
            throw e
        })
        console.log('Server started — waiting for /join request')

        // Keep process alive (server handles everything via HTTP)
    } catch (error) {
        console.error('Fatal startup error:', error)
        exit(1)
    }
})()
