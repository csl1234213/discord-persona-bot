class StyleValidator:
    def validate_length(self, answer: str, maximum: int = 8000) -> bool:
        return 0 < len(answer.strip()) <= maximum
