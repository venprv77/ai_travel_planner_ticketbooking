-- AI Travel Planner MySQL schema
-- Shared planner cache + shared ticket inventory.

CREATE DATABASE IF NOT EXISTS travel_planner
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE travel_planner;

CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'user',
    created_at DATETIME NOT NULL
) ENGINE=InnoDB;

-- One shared AI planner result for one exact search.
-- search_key includes source, destination, date, days, travelers, style and interests.
CREATE TABLE IF NOT EXISTS planner_searches (
    search_key VARCHAR(64) PRIMARY KEY,
    source VARCHAR(255) NOT NULL,
    destination VARCHAR(255) NOT NULL,
    travel_date DATE NOT NULL,
    days INT NOT NULL,
    travelers INT NOT NULL,
    travel_style VARCHAR(30) NOT NULL,
    interests TEXT NOT NULL,
    budget DECIMAL(14,2) DEFAULT 0,
    result_json LONGTEXT NOT NULL,
    created_at DATETIME NOT NULL,
    INDEX idx_planner_route_date (source, destination, travel_date),
    INDEX idx_planner_inputs (source, destination, travel_date, days, travelers, travel_style)
) ENGINE=InnoDB;

-- Per-user history record. It points to the same generated result by storing result_json.
CREATE TABLE IF NOT EXISTS trips (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    source VARCHAR(255) NOT NULL,
    destination VARCHAR(255) NOT NULL,
    days INT NOT NULL,
    travel_date DATE NOT NULL,
    travelers INT NOT NULL,
    travel_style VARCHAR(30) NOT NULL,
    interests TEXT,
    budget DECIMAL(14,2) DEFAULT 0,
    result_json LONGTEXT NOT NULL,
    created_at DATETIME NOT NULL,
    CONSTRAINT fk_trips_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_trips_user (user_id),
    INDEX idx_trips_search (user_id, source, destination, travel_date, travelers)
) ENGINE=InnoDB;

-- One shared ticket search for route/date/transport type.
CREATE TABLE IF NOT EXISTS ticket_searches (
    search_key VARCHAR(64) PRIMARY KEY,
    source VARCHAR(255) NOT NULL,
    destination VARCHAR(255) NOT NULL,
    travel_date DATE NOT NULL,
    vehicle_type VARCHAR(30) NOT NULL,
    created_at DATETIME NOT NULL,
    INDEX idx_ticket_search_route (source, destination, travel_date, vehicle_type)
) ENGINE=InnoDB;

-- Ticket options belonging to a shared search.
CREATE TABLE IF NOT EXISTS ticket_results (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NULL,
    search_key VARCHAR(64) NOT NULL,
    source VARCHAR(255) NOT NULL,
    destination VARCHAR(255) NOT NULL,
    travel_date DATE NOT NULL,
    vehicle_type VARCHAR(30) NOT NULL,
    ticket_json LONGTEXT NOT NULL,
    created_at DATETIME NOT NULL,
    CONSTRAINT fk_ticket_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT fk_ticket_search FOREIGN KEY (search_key) REFERENCES ticket_searches(search_key) ON DELETE CASCADE,
    INDEX idx_ticket_search_key (search_key),
    INDEX idx_ticket_route_date (source, destination, travel_date, vehicle_type)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS bookings (
    id VARCHAR(20) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    ticket_result_id VARCHAR(36) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'Booked',
    created_at DATETIME NOT NULL,
    cancelled_at DATETIME NULL,
    CONSTRAINT fk_booking_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_booking_ticket FOREIGN KEY (ticket_result_id) REFERENCES ticket_results(id) ON DELETE RESTRICT,
    INDEX idx_bookings_user (user_id),
    INDEX idx_bookings_ticket (ticket_result_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS passengers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    booking_id VARCHAR(20) NOT NULL,
    name VARCHAR(120) NOT NULL,
    seat VARCHAR(30) NOT NULL,
    CONSTRAINT fk_passenger_booking FOREIGN KEY (booking_id) REFERENCES bookings(id) ON DELETE CASCADE,
    UNIQUE KEY uq_booking_seat (booking_id, seat),
    INDEX idx_passengers_booking (booking_id)
) ENGINE=InnoDB;

-- Shared seat inventory. One seat can be booked only once for a ticket option.
CREATE TABLE IF NOT EXISTS booked_seats (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ticket_result_id VARCHAR(36) NOT NULL,
    booking_id VARCHAR(20) NOT NULL,
    seat VARCHAR(30) NOT NULL,
    created_at DATETIME NOT NULL,
    CONSTRAINT fk_booked_seat_ticket FOREIGN KEY (ticket_result_id) REFERENCES ticket_results(id) ON DELETE CASCADE,
    CONSTRAINT fk_booked_seat_booking FOREIGN KEY (booking_id) REFERENCES bookings(id) ON DELETE CASCADE,
    UNIQUE KEY uq_ticket_seat (ticket_result_id, seat),
    UNIQUE KEY uq_booking_seat_global (booking_id, seat),
    INDEX idx_booked_seat_ticket (ticket_result_id)
) ENGINE=InnoDB;
