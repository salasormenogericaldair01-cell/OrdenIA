from .plain_text import PlainTextExtractor


class SourceCodeExtractor(PlainTextExtractor):
    def __init__(self) -> None:
        super().__init__("Código")
