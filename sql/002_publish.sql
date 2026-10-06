SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO
CREATE OR ALTER PROCEDURE dbo.PublishReviewedRun
    @RunId nvarchar(64)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    DECLARE @OwnTransaction bit = CASE WHEN @@TRANCOUNT = 0 THEN 1 ELSE 0 END;
    DECLARE @Period char(7), @LockResult int, @Resource nvarchar(255);
    BEGIN TRY
        IF @OwnTransaction = 1 BEGIN TRANSACTION;
        ELSE SAVE TRANSACTION PublishReviewedRun;

        SELECT @Period = Period FROM dbo.EtlRun WHERE RunId = @RunId;
        IF @Period IS NULL THROW 51001, 'Run does not exist.', 1;
        SET @Resource = CONCAT('PeopleOps:publication:', @Period);
        EXEC @LockResult = sys.sp_getapplock
            @Resource = @Resource, @LockMode = 'Exclusive',
            @LockOwner = 'Transaction', @LockTimeout = 10000;
        IF @LockResult < 0 THROW 51002, 'Could not acquire monthly publication lock.', 1;

        /* Reject partial imports before making them visible as a published month. */
        IF NOT EXISTS (SELECT 1 FROM dbo.RunEvidenceSeal WHERE RunId = @RunId)
            THROW 51003, 'Run import has not been sealed.', 1;
        IF EXISTS (
            SELECT 1 FROM dbo.EtlRun r WHERE r.RunId = @RunId AND (
                r.Headcount <> (SELECT COUNT(*) FROM dbo.PayrollFact f WHERE f.RunId = r.RunId)
                OR r.GrossCents <> COALESCE((SELECT SUM(f.GrossCents) FROM dbo.PayrollFact f WHERE f.RunId = r.RunId), 0)
                OR r.ExpectedCents <> COALESCE((SELECT SUM(f.ExpectedCents) FROM dbo.PayrollFact f WHERE f.RunId = r.RunId), 0)
                OR r.VarianceCents <> COALESCE((SELECT SUM(f.VarianceCents) FROM dbo.PayrollFact f WHERE f.RunId = r.RunId), 0)
                OR r.OvertimeMinutes <> COALESCE((SELECT SUM(CAST(f.OvertimeMinutes AS bigint)) FROM dbo.PayrollFact f WHERE f.RunId = r.RunId), 0)
                OR r.WarningCount <> (SELECT COUNT(*) FROM dbo.QualityFinding f WHERE f.RunId = r.RunId AND f.Severity = 'warning')
                OR r.CriticalCount <> (SELECT COUNT(*) FROM dbo.QualityFinding f WHERE f.RunId = r.RunId AND f.Severity = 'critical')
            )
        ) THROW 51003, 'Incomplete or inconsistent run evidence.', 1;

        /* Never trust the advertised count alone; inspect persisted findings. */
        IF EXISTS (SELECT 1 FROM dbo.EtlRun WHERE RunId = @RunId
                   AND (CriticalCount > 0 OR Outcome = 'blocked'))
           OR EXISTS (SELECT 1 FROM dbo.QualityFinding WHERE RunId = @RunId AND Severity = 'critical')
        BEGIN
            DECLARE @PriorRunId nvarchar(64);
            SELECT @PriorRunId = RunId FROM dbo.MonthlyPublication WHERE Period = @Period;
            IF @OwnTransaction = 1 COMMIT TRANSACTION;
            SELECT @RunId AS run_id, CAST(0 AS bit) AS published,
                   'blocked' AS publication_status, @PriorRunId AS published_run_id;
            RETURN;
        END;

        IF NOT EXISTS (SELECT 1 FROM dbo.MonthlyPublication WHERE Period = @Period AND RunId = @RunId)
        BEGIN
            UPDATE dbo.MonthlyPublication WITH (UPDLOCK, HOLDLOCK)
                SET RunId = @RunId, PublishedAt = SYSDATETIMEOFFSET() WHERE Period = @Period;
            IF @@ROWCOUNT = 0
                INSERT dbo.MonthlyPublication(Period, RunId, PublishedAt)
                VALUES (@Period, @RunId, SYSDATETIMEOFFSET());
        END;
        IF @OwnTransaction = 1 COMMIT TRANSACTION;
        SELECT @RunId AS run_id, CAST(1 AS bit) AS published,
               'published' AS publication_status, @RunId AS published_run_id;
    END TRY
    BEGIN CATCH
        IF @OwnTransaction = 1 AND XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        ELSE IF @OwnTransaction = 0 AND XACT_STATE() = 1 ROLLBACK TRANSACTION PublishReviewedRun;
        /* A doomed caller transaction must be rolled back by its owner. */
        THROW;
    END CATCH;
END;
GO
