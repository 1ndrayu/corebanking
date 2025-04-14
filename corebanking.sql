CREATE DATABASE corebanking;
CREATE TABLE users (id SERIAL PRIMARY KEY, full_name VARCHAR(255), email VARCHAR(255) UNIQUE, phone VARCHAR(20), kyc_status VARCHAR(20), created_at TIMESTAMPTZ);
CREATE TABLE accounts (id SERIAL PRIMARY KEY, user_id INT, account_type VARCHAR(50), account_number VARCHAR(20) UNIQUE, status VARCHAR(50), created_at TIMESTAMPTZ);
CREATE TABLE transactions (id SERIAL PRIMARY KEY, from_account_id INT, to_account_id INT, amount DECIMAL(12, 2), txn_type VARCHAR(50), reference_note VARCHAR(255), status VARCHAR(50), created_at TIMESTAMPTZ);
CREATE TABLE balances (account_id INT PRIMARY KEY, available_balance DECIMAL(12, 2), last_updated TIMESTAMPTZ);
CREATE TABLE ml_flags (id SERIAL PRIMARY KEY, txn_id INT, flag_type VARCHAR(50), model_score DECIMAL(5,2), is_resolved BOOLEAN, created_at TIMESTAMPTZ);
