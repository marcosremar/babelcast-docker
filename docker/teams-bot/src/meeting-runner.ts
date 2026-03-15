import { Api } from './api/methods'
import { Events } from './events'
import { GLOBAL } from './singleton'
import { MeetingStateMachine } from './state-machine/machine'
import { detectMeetingProvider } from './utils/detectMeetingProvider'
import {
    setupFileLogging,
    uploadLogsToS3,
    formatError,
} from './utils/Logger'
import { PathManager } from './utils/PathManager'
import {
    shouldAttemptRetry,
    buildRetryMessage,
    requeueToSQS,
    formatRetryErrorMessage,
    MAX_RETRY_COUNT,
} from './utils/retry-handler'

import { getErrorMessageFromCode } from './state-machine/types'
import { MeetingParams } from './types'

// Track whether a meeting is currently active
let meetingActive = false

/**
 * Start a meeting with the given parameters.
 * Called from the /join HTTP endpoint (server.ts).
 */
export async function startMeeting(meetingParams: MeetingParams): Promise<void> {
    if (meetingActive) {
        throw new Error('A meeting is already active')
    }
    meetingActive = true

    try {
        // Reset singleton state from any previous meeting
        GLOBAL.reset()

        // Detect the meeting provider
        meetingParams.meetingProvider = detectMeetingProvider(
            meetingParams.meeting_url,
        )
        GLOBAL.set(meetingParams)
        PathManager.getInstance().initializePaths()
        setupFileLogging()

        // Log all meeting parameters (masking sensitive data)
        const logParams = { ...meetingParams }
        if (logParams.user_token) logParams.user_token = '***MASKED***'
        if (logParams.bots_api_key) logParams.bots_api_key = '***MASKED***'
        if (logParams.speech_to_text_api_key)
            logParams.speech_to_text_api_key = '***MASKED***'
        if (logParams.zoom_sdk_pwd) logParams.zoom_sdk_pwd = '***MASKED***'
        if (logParams.secret) logParams.secret = '***MASKED***'

        console.log(
            'Received meeting parameters:',
            JSON.stringify(logParams, null, 2),
        )

        // Initialize components (always create fresh instance)
        MeetingStateMachine.instance = null
        MeetingStateMachine.init()
        Events.init()
        Events.joiningCall()

        // Create API instance for non-serverless mode
        if (!GLOBAL.isServerless()) {
            new Api()
        }

        // Start the meeting recording
        await MeetingStateMachine.instance.startRecordMeeting()

        // Handle recording result
        if (MeetingStateMachine.instance.wasRecordingSuccessful()) {
            await handleSuccessfulRecording()
        } else {
            await handleFailedRecording()
        }
    } catch (error) {
        // Handle explicit errors from state machine
        console.error(
            'Meeting failed:',
            error instanceof Error ? error.message : error,
        )
        await handleFailedRecording()
    } finally {
        meetingActive = false
        if (!GLOBAL.isServerless()) {
            try {
                await uploadLogsToS3({})
            } catch (error) {
                console.error('Failed to upload logs to S3:', formatError(error))
            }
        }
        console.log('Meeting session ended')
    }
}

export function isMeetingActive(): boolean {
    return meetingActive
}

/**
 * Handle successful recording completion
 */
async function handleSuccessfulRecording(): Promise<void> {
    console.log(`${Date.now()} Finalize project && Sending WebHook complete`)

    console.log(
        `Recording ended normally with reason: ${MeetingStateMachine.instance.getEndReason()}`,
    )

    if (!GLOBAL.isServerless()) {
        await Api.instance.handleEndMeetingWithRetry()
    }

    await Events.recordingSucceeded()
}

/**
 * Handle failed recording
 */
async function handleFailedRecording(): Promise<void> {
    console.error('Recording did not complete successfully')

    const endReason = GLOBAL.getEndReason()
    const originalErrorMessage = GLOBAL.getErrorMessage()
    const currentRetryCount = GLOBAL.getRetryCount()

    console.log(`Recording failed with reason: ${endReason || 'Unknown'}`)
    console.log(`Error message: ${originalErrorMessage || 'None'}`)
    console.log(`Should retry: ${GLOBAL.getShouldRetry()}`)
    console.log(`Current retry count: ${currentRetryCount}/${MAX_RETRY_COUNT}`)

    if (GLOBAL.isServerless()) {
        console.log('Serverless mode - skipping retry logic')
        const errorMessage =
            originalErrorMessage ||
            (endReason
                ? getErrorMessageFromCode(endReason)
                : 'Recording did not complete successfully')
        await Events.recordingFailed(errorMessage)
        console.log(`Error webhook sent`)
        return
    }

    const shouldRetry = shouldAttemptRetry(currentRetryCount)

    if (shouldRetry) {
        console.log(
            `Error marked as retryable - attempting retry ${currentRetryCount + 1}/${MAX_RETRY_COUNT}`
        )

        try {
            const retryMessage = buildRetryMessage()
            await requeueToSQS(retryMessage)

            const retryErrorMessage = formatRetryErrorMessage(
                originalErrorMessage || 'Recording failed',
                currentRetryCount
            )
            await Events.recordingFailed(retryErrorMessage)

            console.log(
                `Job requeued successfully - exiting without calling backend`
            )
            return
        } catch (error) {
            console.error(
                `Failed to requeue message:`,
                error instanceof Error ? error.message : error
            )
            console.log(`Falling back to normal failure flow`)
        }
    } else {
        if (GLOBAL.getShouldRetry()) {
            console.log(
                `Maximum retry attempts reached (${currentRetryCount}/${MAX_RETRY_COUNT}) - reporting failure`
            )
        } else {
            console.log(`Error not retryable - reporting failure immediately`)
        }
    }

    const errorMessage =
        originalErrorMessage ||
        (endReason
            ? getErrorMessageFromCode(endReason)
            : 'Recording did not complete successfully')

    await Events.recordingFailed(errorMessage)

    console.log(`Sending error to backend`)

    if (!GLOBAL.isServerless() && Api.instance) {
        await Api.instance.notifyRecordingFailure()
    }
    console.log(`Error sent to backend successfully`)
}
