-- Run once to set up the database and tables.

IF NOT EXISTS (SELECT * FROM sys.databases WHERE name = 'invoiceiq')
BEGIN
    CREATE DATABASE invoiceiq;
END
GO

USE invoiceiq;
GO

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'Vendors')
BEGIN
    CREATE TABLE Vendors (
        vendor_id     INT IDENTITY(1,1) PRIMARY KEY,
        vendor_name   NVARCHAR(255) NOT NULL,
        gstin         VARCHAR(15) NULL,
        created_at    DATETIME2 DEFAULT SYSUTCDATETIME()
    );
END
GO

IF NOT EXISTS (SELECT * FROM sys.indexes WHERE name = 'UQ_Vendors_Gstin')
BEGIN
    SET QUOTED_IDENTIFIER ON;
    CREATE UNIQUE INDEX UQ_Vendors_Gstin ON Vendors(gstin) WHERE gstin IS NOT NULL;
END
GO

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'Invoices')
BEGIN
    CREATE TABLE Invoices (
        invoice_id      INT IDENTITY(1,1) PRIMARY KEY,
        vendor_id       INT NOT NULL,
        document_id     VARCHAR(50) NOT NULL UNIQUE,
        invoice_number  NVARCHAR(100) NOT NULL,
        invoice_date    DATE NULL,
        subtotal        DECIMAL(12,2) NULL,
        tax_amount      DECIMAL(12,2) NULL,
        total_amount    DECIMAL(12,2) NOT NULL,
        currency        VARCHAR(10) NULL,
        status          VARCHAR(20) NOT NULL,
        created_at      DATETIME2 DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_Invoices_Vendors FOREIGN KEY (vendor_id)
            REFERENCES Vendors(vendor_id)
    );
END
GO

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'Payments')
BEGIN
    CREATE TABLE Payments (
        payment_id      INT IDENTITY(1,1) PRIMARY KEY,
        invoice_id      INT NOT NULL,
        payment_date    DATE NULL,
        amount          DECIMAL(12,2) NOT NULL,
        payment_status  VARCHAR(20) NOT NULL,
        created_at      DATETIME2 DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_Payments_Invoices FOREIGN KEY (invoice_id)
            REFERENCES Invoices(invoice_id)
    );
END
GO