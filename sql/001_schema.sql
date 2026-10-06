/* Run in an existing, dedicated SQL Server database. Requires SQL Server 2019+.
   Synthetic portfolio reconciliation; this is not a payroll payment system. */
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

CREATE TABLE dbo.EtlRun (
    RunId nvarchar(64) NOT NULL CONSTRAINT PK_EtlRun PRIMARY KEY,
    Period char(7) NOT NULL,
    SourceHash char(64) NOT NULL,
    RuleVersion nvarchar(80) COLLATE Latin1_General_100_BIN2 NOT NULL,
    Scenario nvarchar(32) NOT NULL,
    Outcome nvarchar(24) NOT NULL,
    StartedAt datetimeoffset(3) NOT NULL,
    ImportedAt datetimeoffset(3) NOT NULL CONSTRAINT DF_EtlRun_ImportedAt DEFAULT SYSDATETIMEOFFSET(),
    DurationMs bigint NOT NULL,
    InputRows int NOT NULL,
    AcceptedRows int NOT NULL,
    RejectedRows int NOT NULL,
    CriticalCount int NOT NULL,
    WarningCount int NOT NULL,
    Headcount int NOT NULL,
    GrossCents bigint NOT NULL,
    ExpectedCents bigint NOT NULL,
    VarianceCents bigint NOT NULL,
    OvertimeMinutes bigint NOT NULL,
    CONSTRAINT UQ_EtlRun_Input UNIQUE (Period, SourceHash, RuleVersion),
    CONSTRAINT CK_EtlRun_Period CHECK (Period LIKE '[12][0-9][0-9][0-9]-[01][0-9]' AND RIGHT(Period, 2) BETWEEN '01' AND '12'),
    CONSTRAINT CK_EtlRun_Counts CHECK (
        DurationMs >= 0 AND InputRows >= 0 AND AcceptedRows >= 0 AND RejectedRows >= 0
        AND CriticalCount >= 0 AND WarningCount >= 0 AND Headcount >= 0 AND OvertimeMinutes >= 0),
    CONSTRAINT CK_EtlRun_Outcome CHECK (Outcome IN ('eligible', 'blocked'))
);
GO
CREATE TABLE dbo.PayrollFact (
    RunId nvarchar(64) NOT NULL,
    EmployeeId nvarchar(64) NOT NULL,
    EmployeeName nvarchar(200) NOT NULL,
    Department nvarchar(120) NOT NULL,
    ExpectedCents bigint NOT NULL,
    GrossCents bigint NOT NULL,
    VarianceCents bigint NOT NULL,
    OvertimeMinutes int NOT NULL,
    ReconciliationStatus nvarchar(32) NOT NULL,
    CONSTRAINT PK_PayrollFact PRIMARY KEY (RunId, EmployeeId),
    CONSTRAINT FK_PayrollFact_Run FOREIGN KEY (RunId) REFERENCES dbo.EtlRun(RunId),
    CONSTRAINT CK_PayrollFact_Amounts CHECK (ExpectedCents >= 0 AND GrossCents >= 0 AND OvertimeMinutes >= 0),
    CONSTRAINT CK_PayrollFact_Variance CHECK (VarianceCents = GrossCents - ExpectedCents)
);
CREATE INDEX IX_PayrollFact_RunDepartment ON dbo.PayrollFact(RunId, Department)
INCLUDE (GrossCents, ExpectedCents, VarianceCents, OvertimeMinutes);
GO
CREATE TABLE dbo.QualityFinding (
    RunId nvarchar(64) NOT NULL,
    FindingId nvarchar(128) NOT NULL,
    EmployeeId nvarchar(64) NULL,
    EmployeeName nvarchar(200) NULL,
    Department nvarchar(120) NULL,
    Rule nvarchar(80) NOT NULL,
    Severity nvarchar(16) NOT NULL,
    Message nvarchar(2000) NOT NULL,
    Owner nvarchar(120) NOT NULL,
    Remediation nvarchar(2000) NOT NULL,
    ImpactCents bigint NOT NULL,
    CONSTRAINT PK_QualityFinding PRIMARY KEY (RunId, FindingId),
    CONSTRAINT FK_QualityFinding_Run FOREIGN KEY (RunId) REFERENCES dbo.EtlRun(RunId),
    CONSTRAINT CK_QualityFinding_Severity CHECK (Severity IN ('critical', 'warning'))
);
/* EmployeeId deliberately has no employee FK: unknown-employee findings are evidence. */
CREATE INDEX IX_QualityFinding_RunSeverity ON dbo.QualityFinding(RunId, Severity)
INCLUDE (EmployeeId, Rule, ImpactCents);
GO
CREATE TABLE dbo.RunSource (
    RunId nvarchar(64) NOT NULL,
    SourceName nvarchar(120) NOT NULL,
    SourceType nvarchar(80) NOT NULL,
    RowCount int NOT NULL,
    SourceStatus nvarchar(32) NOT NULL,
    CONSTRAINT PK_RunSource PRIMARY KEY (RunId, SourceName),
    CONSTRAINT FK_RunSource_Run FOREIGN KEY (RunId) REFERENCES dbo.EtlRun(RunId),
    CONSTRAINT CK_RunSource_Rows CHECK (RowCount >= 0)
);
GO
CREATE TABLE dbo.MonthlyPublication (
    Period char(7) NOT NULL CONSTRAINT PK_MonthlyPublication PRIMARY KEY,
    RunId nvarchar(64) NOT NULL,
    PublishedAt datetimeoffset(3) NOT NULL,
    CONSTRAINT FK_MonthlyPublication_Run FOREIGN KEY (RunId) REFERENCES dbo.EtlRun(RunId)
);
GO
/* Insert last in the import transaction. Sealed runs cannot acquire new rows. */
CREATE TABLE dbo.RunEvidenceSeal (
    RunId nvarchar(64) NOT NULL CONSTRAINT PK_RunEvidenceSeal PRIMARY KEY,
    SealedAt datetimeoffset(3) NOT NULL CONSTRAINT DF_RunEvidenceSeal_SealedAt DEFAULT SYSDATETIMEOFFSET(),
    CONSTRAINT FK_RunEvidenceSeal_Run FOREIGN KEY (RunId) REFERENCES dbo.EtlRun(RunId)
);
GO
CREATE TRIGGER dbo.TR_RunEvidenceSeal_Immutable ON dbo.RunEvidenceSeal INSTEAD OF UPDATE, DELETE AS
BEGIN
    THROW 51000, 'Run evidence seals are immutable.', 1;
END;
GO
CREATE TRIGGER dbo.TR_PayrollFact_Sealed ON dbo.PayrollFact AFTER INSERT AS
BEGIN
    IF EXISTS (SELECT 1 FROM inserted i JOIN dbo.RunEvidenceSeal s ON s.RunId = i.RunId)
        THROW 51000, 'Cannot append facts to a sealed run.', 1;
END;
GO
CREATE TRIGGER dbo.TR_QualityFinding_Sealed ON dbo.QualityFinding AFTER INSERT AS
BEGIN
    IF EXISTS (SELECT 1 FROM inserted i JOIN dbo.RunEvidenceSeal s ON s.RunId = i.RunId)
        THROW 51000, 'Cannot append findings to a sealed run.', 1;
END;
GO
CREATE TRIGGER dbo.TR_RunSource_Sealed ON dbo.RunSource AFTER INSERT AS
BEGIN
    IF EXISTS (SELECT 1 FROM inserted i JOIN dbo.RunEvidenceSeal s ON s.RunId = i.RunId)
        THROW 51000, 'Cannot append lineage to a sealed run.', 1;
END;
GO
/* Immutability also applies to accidental updates and deletes by the importer. */
CREATE TRIGGER dbo.TR_EtlRun_Immutable ON dbo.EtlRun INSTEAD OF UPDATE, DELETE AS
BEGIN
    THROW 51000, 'Run evidence is immutable; import a new run for corrected inputs.', 1;
END;
GO
CREATE TRIGGER dbo.TR_PayrollFact_Immutable ON dbo.PayrollFact INSTEAD OF UPDATE, DELETE AS
BEGIN
    THROW 51000, 'Payroll facts are immutable.', 1;
END;
GO
CREATE TRIGGER dbo.TR_QualityFinding_Immutable ON dbo.QualityFinding INSTEAD OF UPDATE, DELETE AS
BEGIN
    THROW 51000, 'Quality findings are immutable.', 1;
END;
GO
CREATE TRIGGER dbo.TR_RunSource_Immutable ON dbo.RunSource INSTEAD OF UPDATE, DELETE AS
BEGIN
    THROW 51000, 'Source lineage is immutable.', 1;
END;
GO
