package com.driftshield.backend.repository.drift;

import com.driftshield.backend.entity.drift.DriftEvent;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

@Repository
public interface DriftEventRepository
        extends JpaRepository<DriftEvent, Long> {
}
