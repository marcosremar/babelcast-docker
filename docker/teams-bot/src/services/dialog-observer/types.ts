export interface DialogObserverResult {
    found: boolean
    dismissed: boolean
    modalType: string | null
    detectionMethod?: string
    language?: string
}
