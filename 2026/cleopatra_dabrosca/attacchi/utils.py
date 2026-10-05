from typing import Any


class ReportTable:

    headers: list[str]
    rows: list[list[str|int|float]]
    columns_length: list[int]

    def __init__(self,
                 headers: list[str] =
                    ["Metrica", "Baseline", "DoS"]
                ) -> None:
        self.headers = headers
        self.rows = list[list[str|int|float]]()
        self.columns_length = list[int]()

        for header in headers:
            self.columns_length.append(len(header))


    def addRow(self, row: list[str|int|float]) -> None:
        if len(row) != len(self.headers):
            raise ValueError("ROW: Wrong length. Expected {}, got {}".format(len(self.headers), len(row)))

        self.rows.append(row)

        for index, entry in enumerate(row):
            entry_len = len(str(entry))
            if entry_len > self.columns_length[index]:
                self.columns_length[index] = entry_len

    def __str__(self) -> str:
        line_length = sum(self.columns_length) + 3*(len(self.headers) - 1)

        result = ""
        result += "="*line_length + "\n"
        result += f"{'REPORT':^{line_length}}" + "\n"
        result += "="*line_length + "\n"
        result += " | ".join(map(lambda header, column_length: f"{header:<{column_length}}", self.headers, self.columns_length)) + "\n"
        result += "-"*line_length + "\n"
        for row in self.rows:
            result += " | ".join(map(lambda row, column_length: f"{row:<{column_length}}", row, self.columns_length)) + "\n"
        result += "="*line_length + "\n"

        return result
