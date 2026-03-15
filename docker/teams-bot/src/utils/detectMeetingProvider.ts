import { MeetingProvider } from '../types'

export function detectMeetingProvider(url: string): MeetingProvider {
    if (url.includes('https://teams')) {
        return 'Teams'
    } else if (url.includes('https://meet')) {
        return 'Meet'
    } else {
        throw new Error('Unsupported meeting provider')
    }
}
