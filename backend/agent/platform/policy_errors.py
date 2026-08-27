class PlatformToolError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ApprovalRequired(PlatformToolError):
    def __init__(self, approval_id):
        self.approval_id = approval_id
        super().__init__("APPROVAL_REQUIRED")
